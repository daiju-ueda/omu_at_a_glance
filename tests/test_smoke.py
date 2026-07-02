import pytest

from collector.openalex import OpenAlexClient
from collector.parse import parse_author, parse_work
from collector.sync import AUTHOR_SELECT, INSTITUTION_ID, WORK_SELECT


@pytest.mark.smoke
def test_real_api_author_page():
    client = OpenAlexClient()
    rec = next(client.paginate(
        "authors", f"last_known_institutions.id:{INSTITUTION_ID}",
        select=AUTHOR_SELECT, per_page=2))
    kw = parse_author(rec)
    assert kw["openalex_id"].startswith("A")


@pytest.mark.smoke
def test_real_api_work_page():
    client = OpenAlexClient()
    rec = next(client.paginate(
        "works",
        f"institutions.id:{INSTITUTION_ID},from_publication_date:2023-07-01",
        select=WORK_SELECT, per_page=2))
    work_kw, auths = parse_work(rec)
    assert work_kw["openalex_id"].startswith("W")
    assert len(auths) >= 1
