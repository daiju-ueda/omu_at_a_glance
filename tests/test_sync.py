import copy
import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collector.sync import sync_authors, sync_works, window_start
from db.models import Authorship, Researcher, SyncState, Work, get_engine
from tests.test_parse import AUTHOR, WORK


class FakeClient:
    def __init__(self, results, count_value=None):
        self.results = results
        self.count_value = count_value
        self.calls = []

    def paginate(self, endpoint, filter_str, select=None, per_page=200):
        self.calls.append((endpoint, filter_str))
        yield from self.results

    def count(self, endpoint, filter_str):
        return self.count_value


TODAY = datetime.date(2026, 7, 2)


def test_window_start():
    assert window_start(TODAY) == "2023-07-02"


def test_window_start_leap_day():
    assert window_start(datetime.date(2028, 2, 29)) == "2025-02-28"


def test_sync_authors_upserts_and_records_state():
    engine = get_engine(":memory:")
    client = FakeClient([AUTHOR], count_value=1)
    with Session(engine) as s:
        n = sync_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A5023888391").h_index == 25
        assert s.get(SyncState, "authors").last_synced_at == "2026-07-02"
        # 再実行しても重複せず更新になる
        sync_authors(s, client, today=TODAY)
        assert s.scalar(select(func.count()).select_from(Researcher)) == 1
    assert "last_known_institutions.id:I4387152983" in client.calls[0][1]


def test_sync_works_upserts_works_and_authorships():
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=1)
    with Session(engine) as s:
        n = sync_works(s, client, today=TODAY)
        assert n == 1
        assert s.get(Work, "W4385564466").cited_by_count == 521
        assert s.scalar(select(func.count()).select_from(Authorship)) == 2
    assert ("institutions.id:I4387152983|I317356780|I15807432|I4210166029"
            in client.calls[0][1])
    assert "from_publication_date:2023-07-02" in client.calls[0][1]


def test_sync_works_stores_institution_ids():
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=1)
    with Session(engine) as s:
        sync_works(s, client, today=TODAY)
        a = s.get(Authorship, ("W4385564466", "A5023888391"))
        assert a.institution_ids == "I4387152983"


def test_sync_authors_sets_source_and_keeps_works_rows():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A_WORKS", display_name="Backfilled",
                         source="works", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeClient([AUTHOR], count_value=1)
        sync_authors(s, client, today=TODAY)
        # works由来の行は削除パスの対象外
        assert s.get(Researcher, "A_WORKS") is not None
        assert s.get(Researcher, "A5023888391").source == "last_known"


def test_sync_works_full_warns_on_count_mismatch(caplog):
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=99)
    with Session(engine) as s:
        s.add(Work(openalex_id="W_STALE", title="", publication_date="2024-05-05",
                   cited_by_count=0, is_top1pct=False, is_top10pct=False, is_oa=False,
                   raw_json="{}", updated_at=""))
        s.commit()
        with caplog.at_level("WARNING"):
            sync_works(s, client, today=TODAY)
        assert s.get(Work, "W_STALE") is not None
    assert any("mismatch" in r.message for r in caplog.records)
    assert any("skipping deletion" in r.message for r in caplog.records)


def test_sync_authors_removes_departed(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A_OLD", display_name="Old Researcher",
                         raw_json="{}", updated_at=""))
        s.commit()
        client = FakeClient([AUTHOR], count_value=1)
        with caplog.at_level("INFO"):
            n = sync_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A_OLD") is None
        assert s.get(Researcher, "A5023888391") is not None
    assert any("removed" in r.message for r in caplog.records)


def test_sync_authors_empty_fetch_skips_deletion(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A_KEEP", display_name="Keep Researcher",
                         raw_json="{}", updated_at=""))
        s.commit()
        client = FakeClient([], count_value=0)
        with caplog.at_level("WARNING"):
            n = sync_authors(s, client, today=TODAY)
        assert n == 0
        assert s.get(Researcher, "A_KEEP") is not None
    assert any("skipping deletion" in r.message for r in caplog.records)


def test_sync_works_removes_stale_and_refreshes_authorships(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        client = FakeClient([WORK], count_value=1)
        n = sync_works(s, client, today=TODAY)
        assert n == 1

        # 直接シード: ウィンドウ内の消滅予定作品、ウィンドウ外の作品（削除対象外）
        s.add(Work(openalex_id="W_OLD", title="stale in-window work",
                   publication_date="2024-05-05", raw_json="{}", updated_at=""))
        s.add(Work(openalex_id="W_OUT_OF_WINDOW", title="old out-of-window work",
                   publication_date="2019-01-01", raw_json="{}", updated_at=""))
        s.commit()

        modified_work = copy.deepcopy(WORK)
        modified_work["authorships"] = [modified_work["authorships"][0]]
        client2 = FakeClient([modified_work], count_value=1)
        with caplog.at_level("INFO"):
            n2 = sync_works(s, client2, today=TODAY)
        assert n2 == 1

        assert s.scalar(
            select(func.count()).select_from(Authorship)
            .where(Authorship.work_id == "W4385564466")) == 1
        assert s.get(Work, "W_OLD") is None
        assert s.get(Work, "W_OUT_OF_WINDOW") is not None
    assert any("removed" in r.message for r in caplog.records)
