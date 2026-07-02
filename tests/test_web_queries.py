from sqlalchemy.orm import Session

from db.models import get_engine
from web.queries import last_synced, ranking, researcher_detail, search


def _session(path):
    return Session(get_engine(path))


def test_ranking_default_filters_and_sorts(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s)
    ids = [r.Researcher.openalex_id for r in rows]
    assert ids == ["A1", "A2"]  # A3はworks<5で除外、NULL FWCIは末尾
    assert total == 2


def test_ranking_min_works_zero_and_sort_switch(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s, min_works=0)
        assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1", "A2"]
        assert total == 3
        rows, _ = ranking(s, sort="total_citations", min_works=0)
        assert [r.Researcher.openalex_id for r in rows][0] == "A2"


def test_ranking_invalid_sort_falls_back(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, _ = ranking(s, sort="evil'; DROP TABLE works;--", min_works=0)
    assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1", "A2"]


def test_ranking_pagination(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s, min_works=0, page=2)
    assert rows == [] and total == 3


def test_researcher_detail(seeded_db_path):
    with _session(seeded_db_path) as s:
        result = researcher_detail(s, "A1")
        assert result is not None
        r, m, works = result
        assert r.display_name == "Taro Yamada"
        assert m.works_count_3y == 10
        # W2はウィンドウ外で除外、被引用数降順
        assert [row.Work.openalex_id for row in works] == ["W1", "W3"]
        assert researcher_detail(s, "NOPE") is None


def test_search(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = search(s, "yama")
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]
        assert search(s, "zzz") == []


def test_last_synced(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert last_synced(s) == "2026-07-02"
