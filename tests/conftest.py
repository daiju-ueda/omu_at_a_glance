import datetime

import pytest
from sqlalchemy.orm import Session

from db.models import (Authorship, Researcher, ResearcherMetrics, SyncState,
                       Work, get_engine)

TODAY = datetime.date.today()
RECENT = (TODAY - datetime.timedelta(days=90)).isoformat()
RECENT2 = (TODAY - datetime.timedelta(days=400)).isoformat()


def _work(id_, title, date, doi, cites, fwci, top1, top10, venue):
    return Work(openalex_id=id_, doi=doi, title=title, publication_date=date,
                venue=venue, type="article", cited_by_count=cites, fwci=fwci,
                cnp_value=None, is_top1pct=top1, is_top10pct=top10,
                topic=None, subfield=None, is_oa=False, raw_json="{}",
                updated_at="")


@pytest.fixture()
def seeded_db_path(tmp_path):
    path = str(tmp_path / "test.db")
    engine = get_engine(path)
    with Session(engine) as s:
        s.add_all([
            Researcher(openalex_id="A1", display_name="Taro Yamada",
                       orcid="0000-0001-1111-1111", h_index=20, works_count=100,
                       raw_json="{}", updated_at=""),
            Researcher(openalex_id="A2", display_name="Hanako Suzuki",
                       orcid=None, h_index=10, works_count=30,
                       raw_json="{}", updated_at=""),
            Researcher(openalex_id="A3", display_name="Ichiro Tanaka",
                       orcid=None, h_index=5, works_count=8,
                       raw_json="{}", updated_at=""),
            Researcher(openalex_id="A4", display_name="Jiro Sato",
                       name_ja="佐藤次郎", orcid=None, h_index=1, works_count=2,
                       raw_json="{}", updated_at=""),
        ])
        s.add_all([
            ResearcherMetrics(researcher_id="A1", works_count_3y=10,
                              total_citations=500, fwci_mean=3.5,
                              fwci_median=2.0, top10pct_count=4,
                              first_author_count=3, corresponding_count=5,
                              computed_at=""),
            ResearcherMetrics(researcher_id="A2", works_count_3y=8,
                              total_citations=900, fwci_mean=None,
                              fwci_median=None, top10pct_count=1,
                              first_author_count=2, corresponding_count=1,
                              computed_at=""),
            ResearcherMetrics(researcher_id="A3", works_count_3y=2,
                              total_citations=50, fwci_mean=9.9,
                              fwci_median=9.9, top10pct_count=2,
                              first_author_count=2, corresponding_count=2,
                              computed_at=""),
        ])
        s.add_all([
            _work("W1", "Deep Learning in Radiology", RECENT, "10.1/x",
                  300, 5.0, True, True, "Nature"),
            _work("W2", "Old Paper Outside Window", "2019-01-01", None,
                  999, 9.0, False, True, "Cell"),
            _work("W3", "Cancer Genomics Study", RECENT2, "10.1/y",
                  50, None, False, False, None),
        ])
        s.add_all([
            Authorship(work_id="W1", author_id="A1", author_position="first",
                       is_corresponding=True),
            Authorship(work_id="W2", author_id="A1", author_position="first",
                       is_corresponding=True),
            Authorship(work_id="W3", author_id="A1", author_position="middle",
                       is_corresponding=False),
            Authorship(work_id="W1", author_id="A2", author_position="last",
                       is_corresponding=False),
        ])
        s.add(SyncState(source="works", cursor=None,
                        last_synced_at="2026-07-02"))
        s.commit()
    return path
