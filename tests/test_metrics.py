import datetime

from sqlalchemy.orm import Session

from collector.metrics import compute_metrics
from db.models import Authorship, Researcher, ResearcherMetrics, Work, get_engine

TODAY = datetime.date(2026, 7, 2)


def _researcher(id_):
    return Researcher(openalex_id=id_, display_name=id_, h_index=1,
                      works_count=1, raw_json="{}", updated_at="")


def _work(id_, date, cites, fwci, top10):
    return Work(openalex_id=id_, title=id_, publication_date=date,
                cited_by_count=cites, fwci=fwci, is_top1pct=False,
                is_top10pct=top10, is_oa=False, raw_json="{}", updated_at="")


def test_compute_metrics():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([_researcher("A1"), _researcher("A2")])
        s.add_all([
            _work("W1", "2024-01-01", 10, 2.0, True),
            _work("W2", "2025-01-01", 4, 1.0, False),
            _work("W3", "2020-01-01", 100, 9.0, True),   # ウィンドウ外
            _work("W4", "2024-06-01", 6, None, False),    # fwci欠損
        ])
        s.add_all([
            Authorship(work_id="W1", author_id="A1", author_position="first",
                       is_corresponding=True),
            Authorship(work_id="W2", author_id="A1", author_position="last",
                       is_corresponding=False),
            Authorship(work_id="W3", author_id="A1", author_position="first",
                       is_corresponding=True),
            Authorship(work_id="W4", author_id="A1", author_position="middle",
                       is_corresponding=False),
            Authorship(work_id="W1", author_id="A9", author_position="middle",
                       is_corresponding=False),  # researchersに居ない外部著者
        ])
        s.commit()

        n = compute_metrics(s, TODAY)
        assert n == 2

        m1 = s.get(ResearcherMetrics, "A1")
        assert m1.works_count_3y == 3          # W1, W2, W4（W3はウィンドウ外）
        assert m1.total_citations == 20        # 10+4+6
        assert m1.fwci_mean == 1.5             # (2.0+1.0)/2, W4のNULLは除外
        assert m1.fwci_median == 1.5
        assert m1.top10pct_count == 1
        assert m1.first_author_count == 1
        assert m1.corresponding_count == 1

        m2 = s.get(ResearcherMetrics, "A2")    # 論文ゼロでも行を持つ
        assert m2.works_count_3y == 0
        assert m2.fwci_mean is None

        assert s.get(ResearcherMetrics, "A9") is None  # 外部著者は集計しない

        compute_metrics(s, TODAY)  # 洗い替え：再実行で重複しない
        assert s.query(ResearcherMetrics).count() == 2
