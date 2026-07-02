from collector.parse import parse_author, parse_work, strip_id

AUTHOR = {
    "id": "https://openalex.org/A5023888391",
    "display_name": "Daiju Ueda",
    "orcid": "https://orcid.org/0000-0002-9181-7968",
    "works_count": 120,
    "summary_stats": {"h_index": 25},
    "updated_date": "2026-06-30T00:00:00",
}

WORK = {
    "id": "https://openalex.org/W4385564466",
    "doi": "https://doi.org/10.1007/s11604-023-01474-3",
    "title": "Fairness of artificial intelligence in healthcare",
    "publication_date": "2023-08-04",
    "type": "article",
    "cited_by_count": 521,
    "fwci": 18.2275,
    "citation_normalized_percentile": {
        "value": 0.99305621, "is_in_top_1_percent": True, "is_in_top_10_percent": True},
    "primary_topic": {"display_name": "AI in Healthcare",
                      "subfield": {"display_name": "Health Informatics"}},
    "primary_location": {"source": {"display_name": "Japanese Journal of Radiology"}},
    "open_access": {"is_oa": True},
    "updated_date": "2026-06-01T00:00:00",
    "authorships": [
        {"author_position": "first", "is_corresponding": True,
         "author": {"id": "https://openalex.org/A5023888391"}},
        {"author_position": "last", "is_corresponding": False,
         "author": {"id": "https://openalex.org/A999"}},
    ],
}


def test_strip_id():
    assert strip_id("https://openalex.org/A1") == "A1"
    assert strip_id("https://orcid.org/0000-0002-9181-7968") == "0000-0002-9181-7968"
    assert strip_id(None) is None


def test_parse_author():
    kw = parse_author(AUTHOR)
    assert kw["openalex_id"] == "A5023888391"
    assert kw["orcid"] == "0000-0002-9181-7968"
    assert kw["h_index"] == 25
    assert kw["works_count"] == 120
    assert '"Daiju Ueda"' in kw["raw_json"]


def test_parse_author_missing_fields():
    kw = parse_author({"id": "https://openalex.org/A1", "display_name": "X"})
    assert kw["orcid"] is None
    assert kw["h_index"] == 0


def test_parse_author_explicit_null_fields():
    kw = parse_author({
        "id": "https://openalex.org/A1", "display_name": "X",
        "works_count": None, "summary_stats": {"h_index": None}})
    assert kw["h_index"] == 0
    assert kw["works_count"] == 0


def test_parse_work():
    work_kw, auths = parse_work(WORK)
    assert work_kw["openalex_id"] == "W4385564466"
    assert work_kw["doi"] == "10.1007/s11604-023-01474-3"
    assert work_kw["venue"] == "Japanese Journal of Radiology"
    assert work_kw["fwci"] == 18.2275
    assert work_kw["cnp_value"] == 0.99305621
    assert work_kw["is_top1pct"] is True and work_kw["is_top10pct"] is True
    assert work_kw["subfield"] == "Health Informatics"
    assert work_kw["is_oa"] is True
    assert len(auths) == 2
    assert auths[0] == {"work_id": "W4385564466", "author_id": "A5023888391",
                        "author_position": "first", "is_corresponding": True}


def test_parse_work_missing_fields():
    work_kw, auths = parse_work({
        "id": "https://openalex.org/W1", "title": None,
        "publication_date": "2024-01-01", "authorships": []})
    assert work_kw["title"] == ""
    assert work_kw["fwci"] is None
    assert work_kw["is_top10pct"] is False
    assert work_kw["venue"] is None
    assert auths == []


def test_parse_work_explicit_null_fields():
    work_kw, _ = parse_work({
        "id": "https://openalex.org/W1", "title": "X",
        "publication_date": "2024-01-01", "cited_by_count": None,
        "authorships": []})
    assert work_kw["cited_by_count"] == 0
