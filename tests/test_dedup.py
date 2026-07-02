import datetime

from sqlalchemy.orm import Session

from collector.dedup import apply_dedup
from db.models import Authorship, Researcher, Work, get_engine

TODAY = datetime.date(2026, 7, 2)


def _r(id_, name, orcid=None, works_count=10, **kw):
    return Researcher(openalex_id=id_, display_name=name, orcid=orcid,
                      h_index=1, works_count=works_count, raw_json="{}",
                      updated_at="", **kw)


def _w(id_):
    return Work(openalex_id=id_, title=id_, publication_date="2024-06-01",
                cited_by_count=0, is_top1pct=False, is_top10pct=False,
                is_oa=False, raw_json="{}", updated_at="")


def _a(work, author):
    return Authorship(work_id=work, author_id=author,
                      author_position="middle", is_corresponding=False)


def _setup(session, researchers, works, auths):
    session.add_all(researchers)
    session.add_all(works)
    session.add_all(auths)
    session.commit()


def test_merge_by_same_orcid():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Hiroaki Nakamura", orcid="0-1", works_count=100),
                   _r("A2", "Hiroaki Nakamura", orcid="0-1", works_count=5)],
               [], [])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A1").canonical_id is None      # 正準（works多）
        assert s.get(Researcher, "A2").canonical_id == "A1"


def test_never_merge_different_orcid_even_with_shared_coauthors():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # 共著者2人の重なりがあってもORCID相違なら結合しない
        _setup(s, [_r("A1", "Taro Sato", orcid="0-1"),
                   _r("A2", "Taro Sato", orcid="0-2"),
                   _r("C1", "Coauthor One"), _r("C2", "Coauthor Two")],
               [_w("W1"), _w("W2")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2")])
        assert apply_dedup(s, TODAY) == 0
        assert s.get(Researcher, "A1").canonical_id is None
        assert s.get(Researcher, "A2").canonical_id is None


def test_merge_by_coauthor_overlap_threshold():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A1/A2: 共通共著者2人 → 結合。A3: 共通1人のみ → 結合しない
        _setup(s, [_r("A1", "Jiro Ito", works_count=50),
                   _r("A2", "Jiro Ito", works_count=10),
                   _r("A3", "Jiro Ito", works_count=5),
                   _r("C1", "Co One"), _r("C2", "Co Two")],
               [_w("W1"), _w("W2"), _w("W3")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2"),
                _a("W3", "A3"), _a("W3", "C1")])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
        assert s.get(Researcher, "A3").canonical_id is None


def test_merge_by_shared_work_and_no_cross_name_merge():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Ken Abe", works_count=50),
                   _r("A2", "Ken Abe", works_count=10),
                   _r("B1", "Ken Aoki", works_count=99)],  # 別名は対象外
               [_w("W1")],
               [_a("W1", "A1"), _a("W1", "A2"), _a("W1", "B1")])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
        assert s.get(Researcher, "B1").canonical_id is None


def test_cluster_with_multiple_orcids_dissolved(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A1(ORCID x)–A2(無)–A3(ORCID y): A2経由で推移的につながるが解散
        _setup(s, [_r("A1", "Yu Mori", orcid="0-x"),
                   _r("A2", "Yu Mori"),
                   _r("A3", "Yu Mori", orcid="0-y"),
                   _r("C1", "Co One"), _r("C2", "Co Two")],
               [_w("W1"), _w("W2"), _w("W3")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2"),
                _a("W3", "A3"), _a("W3", "C1"), _a("W3", "C2")])
        with caplog.at_level("WARNING"):
            assert apply_dedup(s, TODAY) == 0
        assert any("解散" in r.message for r in caplog.records)


def test_alias_attributes_handed_over_and_idempotent():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Rin Kato", orcid="0-1", works_count=100),
                   _r("A2", "Rin Kato", orcid="0-1", works_count=5,
                      name_ja="加藤 凛", department="大学院医学研究科",
                      position="教授", is_official_roster=True)],
               [], [])
        apply_dedup(s, TODAY)
        canon = s.get(Researcher, "A1")
        alias = s.get(Researcher, "A2")
        assert canon.name_ja == "加藤 凛"
        assert canon.department == "大学院医学研究科"
        assert canon.is_official_roster is True
        assert alias.name_ja is None and alias.department is None
        assert alias.is_official_roster is False
        # 冪等
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
