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


def test_sync_authors_upserts_and_records_state():
    engine = get_engine(":memory:")
    client = FakeClient([AUTHOR])
    with Session(engine) as s:
        n = sync_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A5023888391").h_index == 25
        assert s.get(SyncState, "authors").last_synced_at == "2026-07-02"
        # 再実行しても重複せず更新になる
        sync_authors(s, client, today=TODAY)
        assert s.scalar(select(func.count()).select_from(Researcher)) == 1
    assert "last_known_institutions.id:I4387152983" in client.calls[0][1]


def test_sync_authors_incremental_adds_updated_filter():
    engine = get_engine(":memory:")
    client = FakeClient([])
    with Session(engine) as s:
        sync_authors(s, client, today=TODAY, since="2026-06-25")
    assert "from_updated_date:2026-06-25" in client.calls[0][1]


def test_sync_works_upserts_works_and_authorships():
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=1)
    with Session(engine) as s:
        n = sync_works(s, client, today=TODAY)
        assert n == 1
        assert s.get(Work, "W4385564466").cited_by_count == 521
        assert s.scalar(select(func.count()).select_from(Authorship)) == 2
    assert "from_publication_date:2023-07-02" in client.calls[0][1]


def test_sync_works_full_warns_on_count_mismatch(caplog):
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=99)
    with Session(engine) as s:
        with caplog.at_level("WARNING"):
            sync_works(s, client, today=TODAY)
    assert any("mismatch" in r.message for r in caplog.records)
