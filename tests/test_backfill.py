import datetime
import json

from sqlalchemy.orm import Session

from collector.backfill import backfill_authors
from db.models import Researcher, SyncState, Work, get_engine

TODAY = datetime.date(2026, 7, 16)


class FakeAuthorsClient:
    """ids.openalex:A|B|... フィルタに応じて登録済みレコードだけ返す"""

    def __init__(self, records):
        self.records = {r["id"].rsplit("/", 1)[-1]: r for r in records}
        self.calls = []

    def paginate(self, endpoint, filter_str, select=None, per_page=50):
        self.calls.append((endpoint, filter_str))
        assert endpoint == "authors"
        ids = filter_str.removeprefix("ids.openalex:").split("|")
        assert len(ids) <= per_page
        for i in ids:
            if i in self.records:
                yield self.records[i]


def _author(aid, name):
    return {"id": f"https://openalex.org/{aid}", "display_name": name,
            "works_count": 10, "summary_stats": {"h_index": 5},
            "updated_date": "2026-07-01T00:00:00"}


def _seed_work(s, work_id, entries):
    """entries: [(author_id, [raw affiliation strings]), ...]"""
    raw = json.dumps({"authorships": [
        {"author": {"id": f"https://openalex.org/{aid}"},
         "raw_affiliation_strings": raws}
        for aid, raws in entries]})
    s.add(Work(openalex_id=work_id, title="t", publication_date="2025-01-01",
               raw_json=raw, updated_at=""))


OMU_RAW = "Graduate School of Medicine, Osaka Metropolitan University"
OCU_RAW = "Osaka City University, Osaka, Japan"
HANDAI_RAW = "Graduate School of Medicine, Osaka University"  # 阪大 → 対象外


def test_backfill_adds_qualifying_missing_author():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1", [("A_NEW", [OMU_RAW]),   # OMU表記 → 対象
                             ("A_EXT", ["MIT, USA"]),  # 学外 → 対象外
                             ("A_NULL", [])])          # raw無し → 対象外
        s.commit()
        client = FakeAuthorsClient([_author("A_NEW", "New Researcher"),
                                    _author("A_EXT", "External")])
        n = backfill_authors(s, client, today=TODAY)
        assert n == 1
        r = s.get(Researcher, "A_NEW")
        assert r is not None and r.source == "works"
        assert s.get(Researcher, "A_EXT") is None
        assert s.get(Researcher, "A_NULL") is None
        assert s.get(SyncState, "backfill").last_synced_at == "2026-07-16"


def test_backfill_predecessor_raw_qualifies():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1", [("A_OCU", [OCU_RAW])])  # 旧市大表記
        s.commit()
        client = FakeAuthorsClient([_author("A_OCU", "Ocu Researcher")])
        assert backfill_authors(s, client, today=TODAY) == 1
        assert s.get(Researcher, "A_OCU").source == "works"


def test_backfill_ignores_misresolved_osaka_university():
    # OpenAlexが旧市大IDに誤解決していても、rawが阪大なら取り込まない
    # （白井こころ氏のLancet論文のパターン）
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1", [("A_HANDAI", [HANDAI_RAW])])
        s.commit()
        client = FakeAuthorsClient([_author("A_HANDAI", "Handai Researcher")])
        assert backfill_authors(s, client, today=TODAY) == 0
        assert s.get(Researcher, "A_HANDAI") is None


def test_backfill_does_not_refetch_last_known_researchers():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1", [("A_LK", [OMU_RAW])])
        s.add(Researcher(openalex_id="A_LK", display_name="Existing",
                         source="last_known", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeAuthorsClient([_author("A_LK", "Existing")])
        assert backfill_authors(s, client, today=TODAY) == 0
        assert client.calls == []
        assert s.get(Researcher, "A_LK").source == "last_known"


def test_backfill_removes_stale_works_rows_only():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A_STALE: works由来だが資格となるrawなし → 削除
        # A_LK: last_known由来 → 保持
        _seed_work(s, "W1", [("A_STALE", [HANDAI_RAW])])
        s.add(Researcher(openalex_id="A_STALE", display_name="Stale",
                         source="works", raw_json="{}", updated_at=""))
        s.add(Researcher(openalex_id="A_LK", display_name="Keep",
                         source="last_known", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeAuthorsClient([])
        backfill_authors(s, client, today=TODAY)
        assert s.get(Researcher, "A_STALE") is None
        assert s.get(Researcher, "A_LK") is not None


def test_backfill_skips_unfetchable_ids_with_warning(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1", [("A_OK", [OMU_RAW]),
                             ("A_GONE", [OMU_RAW])])  # APIが返さない（マージ消滅）
        s.commit()
        client = FakeAuthorsClient([_author("A_OK", "Ok Researcher")])
        with caplog.at_level("WARNING"):
            n = backfill_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A_OK") is not None
        assert s.get(Researcher, "A_GONE") is None
    assert any("スキップ" in r.message for r in caplog.records)
