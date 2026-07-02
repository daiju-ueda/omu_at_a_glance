import datetime

from sqlalchemy.orm import Session

from collector.metrics import compute_metrics
from db.models import Authorship, Grant, GrantMember, Researcher, ResearcherMetrics, Work, get_engine

TODAY = datetime.date(2026, 7, 2)


def _researcher(id_):
    return Researcher(openalex_id=id_, display_name=id_, h_index=1,
                      works_count=1, raw_json="{}", updated_at="")


def _work(id_, date, cites, fwci, top10, *, top1=False, n_authors=1,
          intl=False, corp=False, oa=False, type_="article", subfield=None,
          truncated=False):
    return Work(openalex_id=id_, title=id_, publication_date=date,
                cited_by_count=cites, fwci=fwci, is_top1pct=top1,
                is_top10pct=top10, is_oa=oa, type=type_, subfield=subfield,
                n_authors=n_authors, is_intl_collab=intl, is_corp_collab=corp,
                is_authors_truncated=truncated,
                raw_json="{}", updated_at="")


def _auth(work_id, author_id, position="middle", corresponding=False):
    return Authorship(work_id=work_id, author_id=author_id,
                      author_position=position, is_corresponding=corresponding)


def test_compute_metrics():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([_researcher("A1"), _researcher("A2"), _researcher("A5")])
        s.add_all([
            _work("W1", "2024-01-01", 10, 2.0, True, top1=True, n_authors=2,
                  intl=True, oa=True, subfield="ML"),
            _work("W2", "2025-01-01", 4, 1.0, False, n_authors=4, corp=True,
                  type_="preprint", subfield="ML"),
            _work("W3", "2020-01-01", 100, 9.0, True),          # ウィンドウ外
            _work("W4", "2024-06-01", 6, None, False, n_authors=0,
                  type_="dataset"),                              # 著者数0→除数1
            _work("W5", "2024-02-01", 1, None, False, subfield="ML"),
            _work("W6", "2024-03-01", 1, None, False, subfield="AI"),
            _work("W7", "2024-04-01", 100, None, False, n_authors=100,
                  truncated=True),                    # 著者数打ち切り（100人）
        ])
        s.add_all([
            _auth("W1", "A1", position="first", corresponding=True),
            _auth("W2", "A1", position="last"),
            _auth("W3", "A1", position="first", corresponding=True),
            _auth("W4", "A1"),
            _auth("W7", "A1"),
            _auth("W1", "A9"),           # researchersに居ない外部共著者
            _auth("W5", "A5"),
            _auth("W6", "A5"),
        ])
        s.add_all([
            Grant(award_id="G1", title="t", total_amount=10_000_000,
                  raw_json="{}", updated_at=""),
            Grant(award_id="G2", title="t", total_amount=3_000_000,
                  raw_json="{}", updated_at=""),
        ])
        s.add_all([
            GrantMember(award_id="G1", erad_id="E1", name_kanji="x",
                        name_kana=None, role="principal",
                        matched_researcher_id="A1"),
            GrantMember(award_id="G2", erad_id="E2", name_kanji="x",
                        name_kana=None, role="co_investigator",
                        matched_researcher_id="A1"),
            GrantMember(award_id="G2", erad_id="E3", name_kanji="y",
                        name_kana=None, role="principal",
                        matched_researcher_id=None),  # 未マッチは集計外
        ])
        s.commit()

        n = compute_metrics(s, TODAY)
        assert n == 3

        m1 = s.get(ResearcherMetrics, "A1")
        # 既存指標（W1, W2, W4, W7がウィンドウ内）
        assert m1.works_count_3y == 4
        assert m1.total_citations == 120
        assert m1.fwci_mean == 1.5
        assert m1.fwci_median == 1.5
        assert m1.top10pct_count == 1
        assert m1.first_author_count == 1
        assert m1.corresponding_count == 1
        # 新指標
        assert m1.top1pct_count == 1
        # W7は著者数打ち切り（is_authors_truncated=True）のため著者数補正系から除外
        assert m1.fractional_works == 1.75          # 1/2 + 1/4 + 1/1
        assert m1.fractional_citations == 12.0      # 10/2 + 4/4 + 6/1
        assert m1.avg_authors == 2.3333             # (2+4+1)/3
        assert m1.intl_collab_rate == 0.25           # W1のみ / 4件
        assert m1.corp_collab_rate == 0.25           # W2のみ / 4件
        assert m1.oa_rate == 0.25                    # W1のみ / 4件
        assert m1.preprint_count == 1               # W2
        assert m1.dataset_software_count == 1       # W4
        assert m1.unique_coauthors == 1             # A9のみ（本人除外）
        assert m1.top_subfield == "ML"
        assert m1.kaken_pi_count == 1
        assert m1.kaken_copi_count == 1
        assert m1.kaken_total_amount == 10_000_000  # 代表課題のみ

        m2 = s.get(ResearcherMetrics, "A2")         # 論文ゼロ
        assert m2.works_count_3y == 0
        assert m2.fwci_mean is None
        assert m2.fractional_works == 0
        assert m2.avg_authors is None
        assert m2.intl_collab_rate is None
        assert m2.oa_rate is None
        assert m2.top_subfield is None
        assert m2.unique_coauthors == 0
        assert m2.kaken_pi_count == 0 and m2.kaken_total_amount == 0

        m5 = s.get(ResearcherMetrics, "A5")         # subfield同数タイ
        assert m5.top_subfield == "AI"              # 辞書順で先

        assert s.get(ResearcherMetrics, "A9") is None

        compute_metrics(s, TODAY)                   # 洗い替え・冪等
        assert s.query(ResearcherMetrics).count() == 3
