import pytest
from sqlalchemy.orm import Session

from db.models import get_engine
from web.queries import (awards_for, compare, department_stats,
                         departments_list, last_synced, metric_ranks, ranking,
                         researcher_detail, search)


def _session(path):
    return Session(get_engine(path))


def test_ranking_default_filters_and_sorts(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s)
    ids = [r.Researcher.openalex_id for r in rows]
    assert ids == ["A1", "A3", "A2"]  # 既定=fwci_total降順: 35.0, 19.8, 0
    assert total == 3
    assert total_all == 3


def test_ranking_min_works_five(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, min_works=5)
    assert [r.Researcher.openalex_id for r in rows] == ["A1", "A2"]
    assert total == 2 and total_all == 3


def test_ranking_min_works_zero_and_sort_switch(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, min_works=0)
        assert [r.Researcher.openalex_id for r in rows] == ["A1", "A3", "A2"]
        assert total == 3
        rows, _, _ = ranking(s, sort="total_citations", min_works=0)
        assert [r.Researcher.openalex_id for r in rows][0] == "A2"


def test_ranking_invalid_sort_falls_back(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, _, _ = ranking(s, sort="evil'; DROP TABLE works;--", min_works=0)
    assert [r.Researcher.openalex_id for r in rows] == ["A1", "A3", "A2"]


def test_ranking_pagination(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, min_works=0, page=2)
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
        rows = search(s, "佐藤")
        assert [r.Researcher.openalex_id for r in rows] == ["A4"]
        assert rows[0].ResearcherMetrics is None  # metrics無しでもouterjoinで出る


def test_last_synced(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert last_synced(s) == "2026-07-02"


@pytest.mark.parametrize("sort_key,expected_first", [
    ("fwci_mean", "A3"),          # 9.9
    ("total_citations", "A2"),    # 900
    ("top10pct_count", "A1"),     # 4
    ("works_count_3y", "A1"),     # 10
    ("fractional_citations", "A2"),  # 300.0
    ("kaken_total_amount", "A1"),  # 75,000,000
    ("fwci_total", "A1"),  # 35.0
])
def test_ranking_all_sort_keys(seeded_db_path, sort_key, expected_first):
    with _session(seeded_db_path) as s:
        rows, _, _ = ranking(s, sort=sort_key, min_works=0)
    assert rows[0].Researcher.openalex_id == expected_first


def test_compare_preserves_order_and_skips_unknown(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = compare(s, ["A3", "NOPE", "A1"])
        assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1"]


def test_compare_outerjoin_and_empty(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = compare(s, ["A4"])
        assert rows[0].Researcher.display_name == "Jiro Sato"
        assert rows[0].ResearcherMetrics is None
        assert compare(s, []) == []


def test_metric_ranks(seeded_db_path):
    with _session(seeded_db_path) as s:
        ranks = metric_ranks(s, "A2")
        # 母数はworks>=1の3人
        assert ranks["total_citations"] == (1, 3)   # 900は最大
        assert "fwci_total" not in ranks            # 値0は順位非表示
        assert "fwci_mean" not in ranks             # 本人値NULLは非表示
        ranks1 = metric_ranks(s, "A1")
        assert ranks1["fwci_mean"] == (2, 3)        # 9.9(A3) > 3.5(A1) > NULL
        assert ranks1["kaken_total_amount"] == (1, 3)


def test_metric_ranks_outside_population(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert metric_ranks(s, "A4") is None    # metrics行なし
        assert metric_ranks(s, "NOPE") is None


def test_ranking_department_filter(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, department="大学院医学研究科",
                                         min_works=0)
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]
        assert total == 1 and total_all == 1
        rows, _, _ = ranking(s, department="存在しない部局", min_works=0)
        assert rows == []


def test_departments_list_and_stats(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert departments_list(s) == ["大学院医学研究科", "大学院情報学研究科"]
        ranked, small = department_stats(s)
        # A1(医学研究科)・A3(情報学研究科)ともに1人 < MIN_DEPT_MEMBERS(5) → 順位対象外
        assert ranked == []
        assert [d["department"] for d in small] == [
            "大学院医学研究科", "大学院情報学研究科"]  # 人数同数(1)のため部局名昇順
        med = small[0]
        assert med["members"] == 1
        assert med["works"] == 10
        assert med["fwci_per_capita"] == 35.0
        assert med["kaken_amount"] == 75_000_000


def test_metric_ranks_ties_share_rank(seeded_db_path):
    from db.models import Researcher, ResearcherMetrics
    with _session(seeded_db_path) as s:
        s.add(Researcher(openalex_id="A9", display_name="Tie Guy", h_index=1,
                         works_count=5, raw_json="{}", updated_at=""))
        s.add(ResearcherMetrics(researcher_id="A9", works_count_3y=5,
                                total_citations=500, computed_at=""))  # A1と同値
        s.commit()
        r1 = metric_ranks(s, "A1")
        r9 = metric_ranks(s, "A9")
        # A2(900)が1位、A1とA9は500で同率2位
        assert r1["total_citations"] == (2, 4)
        assert r9["total_citations"] == (2, 4)


def test_search_excludes_aliases(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = search(s, "yamada")
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]


def test_detail_includes_alias_works_once(seeded_db_path):
    with _session(seeded_db_path) as s:
        _, _, works = researcher_detail(s, "A1")
        ids = [row.Work.openalex_id for row in works]
        assert ids == ["W1", "W3"]      # W3はエイリアス行があっても1回


def test_detail_alias_role_merged_deterministically(seeded_db_path):
    # W3はA1(middle)とA1b(first)の両方が著者 → firstが表示される
    with _session(seeded_db_path) as s:
        _, _, works = researcher_detail(s, "A1")
        w3 = next(row for row in works if row.Work.openalex_id == "W3")
        assert w3.Authorship.author_position == "first"


def test_awards_for(seeded_db_path):
    with _session(seeded_db_path) as s:
        awards = awards_for(s, "A1")
        assert [a.title for a in awards] == ["ベスト研究賞", "古い賞"]  # NULL年は末尾
        assert awards_for(s, "A2") == []
