from collector.parse import (omu_raw_author_ids, parse_author, parse_work,
                             strip_id)

AUTHOR = {
    "id": "https://openalex.org/A5023888391",
    "display_name": "Daiju Ueda",
    "orcid": "https://orcid.org/0000-0002-9181-7968",
    "works_count": 120,
    "summary_stats": {"h_index": 25, "i10_index": 100,
                      "2yr_mean_citedness": 2.5},
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
    "is_authors_truncated": False,
    "updated_date": "2026-06-01T00:00:00",
    "authorships": [
        {"author_position": "first", "is_corresponding": True,
         "author": {"id": "https://openalex.org/A5023888391"},
         "countries": ["JP"],
         "institutions": [{"id": "https://openalex.org/I4387152983",
                           "type": "education"}]},
        {"author_position": "last", "is_corresponding": False,
         "author": {"id": "https://openalex.org/A999"},
         "countries": ["US"],
         "institutions": [{"id": "https://openalex.org/I100",
                           "type": "funder"}]},
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
    assert kw["i10_index"] == 100
    assert kw["two_yr_mean_citedness"] == 2.5


def test_parse_author_missing_fields():
    kw = parse_author({"id": "https://openalex.org/A1", "display_name": "X"})
    assert kw["orcid"] is None
    assert kw["h_index"] == 0
    assert kw["i10_index"] == 0
    assert kw["two_yr_mean_citedness"] is None


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
    assert work_kw["n_authors"] == 2
    assert work_kw["is_intl_collab"] is True   # USの共著者あり
    assert work_kw["is_corp_collab"] is False  # companyなし
    assert work_kw["is_authors_truncated"] is False
    assert len(auths) == 2
    assert auths[0] == {"work_id": "W4385564466", "author_id": "A5023888391",
                        "author_position": "first", "is_corresponding": True,
                        "institution_ids": "I4387152983"}


def test_parse_work_authorship_institution_ids():
    _, auths = parse_work(WORK)
    assert auths[0]["institution_ids"] == "I4387152983"
    assert auths[1]["institution_ids"] == "I100"


def test_parse_work_authorship_without_institutions():
    rec = {"id": "https://openalex.org/W2", "title": "t",
           "publication_date": "2024-01-01",
           "authorships": [{"author": {"id": "https://openalex.org/A1"},
                            "countries": []}]}
    _, auths = parse_work(rec)
    assert auths[0]["institution_ids"] is None


def _raw_rec(entries):
    return {"authorships": [
        {"author": {"id": f"https://openalex.org/{aid}"},
         "raw_affiliation_strings": raws}
        for aid, raws in entries]}


def test_omu_raw_author_ids_matches_omu_and_predecessors():
    rec = _raw_rec([
        ("A1", ["Graduate School of Medicine, Osaka Metropolitan University"]),
        ("A2", ["Osaka City Univ. Hospital, Osaka, Japan"]),
        ("A3", ["Osaka Prefecture University, Sakai"]),
        ("A4", ["大阪公立大学大学院医学研究科"]),
    ])
    assert omu_raw_author_ids(rec) == {"A1", "A2", "A3", "A4"}


def test_omu_raw_author_ids_rejects_lookalikes():
    rec = _raw_rec([
        # 阪大（誤解決の主因）・大阪経済大・高専・raw無しは対象外
        ("A1", ["Graduate School of Medicine, Osaka University"]),
        ("A2", ["Osaka University of Economics"]),
        ("A3", ["Osaka Metropolitan University College of Technology"]),
        ("A4", []),
    ])
    assert omu_raw_author_ids(rec) == set()


def test_omu_raw_author_ids_any_string_qualifies():
    # 併記（阪大とOMU両方の所属を持つ）のはOMU側があるので対象
    rec = _raw_rec([
        ("A1", ["Osaka University", "Osaka Metropolitan University"]),
    ])
    assert omu_raw_author_ids(rec) == {"A1"}


def test_parse_work_missing_fields():
    work_kw, auths = parse_work({
        "id": "https://openalex.org/W1", "title": None,
        "publication_date": "2024-01-01", "authorships": []})
    assert work_kw["title"] == ""
    assert work_kw["fwci"] is None
    assert work_kw["is_top10pct"] is False
    assert work_kw["venue"] is None
    assert work_kw["n_authors"] == 0
    assert work_kw["is_intl_collab"] is False
    assert work_kw["is_corp_collab"] is False
    assert work_kw["is_authors_truncated"] is False
    assert auths == []


def test_parse_work_truncated_flag():
    work_kw, _ = parse_work({"id": "https://openalex.org/W11", "title": "t",
                             "publication_date": "2024-01-01",
                             "is_authors_truncated": True, "authorships": []})
    assert work_kw["is_authors_truncated"] is True


def test_parse_work_truncated_flag_fallback_on_100_authors():
    # OpenAlexのlist/filterエンドポイントはauthorshipsを常に100件で打ち切って
    # 返すが、is_authors_truncatedフラグ自体はほぼ常にFalse/欠損で返る実運用上の
    # 制約があるため、n_authors==100を打ち切りシグナルとして扱う
    auth_list = [
        {"author": {"id": f"https://openalex.org/A{i}"}, "countries": []}
        for i in range(100)
    ]
    work_kw, _ = parse_work({"id": "https://openalex.org/W12", "title": "t",
                             "publication_date": "2024-01-01",
                             "authorships": auth_list})
    assert work_kw["n_authors"] == 100
    assert work_kw["is_authors_truncated"] is True


def test_parse_work_explicit_null_fields():
    work_kw, _ = parse_work({
        "id": "https://openalex.org/W1", "title": "X",
        "publication_date": "2024-01-01", "cited_by_count": None,
        "authorships": []})
    assert work_kw["cited_by_count"] == 0


def test_parse_work_corporate_and_domestic():
    rec = {
        "id": "https://openalex.org/W9",
        "title": "t",
        "publication_date": "2024-01-01",
        "authorships": [
            {"author": {"id": "https://openalex.org/A1"},
             "countries": ["JP"],
             "institutions": [{"id": "https://openalex.org/I1",
                               "type": "company"}]},
            {"author": {"id": "https://openalex.org/A2"},
             "countries": ["JP"], "institutions": []},
        ],
    }
    work_kw, auths = parse_work(rec)
    assert work_kw["n_authors"] == 2
    assert work_kw["is_intl_collab"] is False  # JPのみ
    assert work_kw["is_corp_collab"] is True
    assert len(auths) == 2


def test_parse_work_authorship_without_author_id_still_counted():
    # author.idが無い著者行はauthorshipsから除外されるがn_authorsには数える
    rec = {
        "id": "https://openalex.org/W10",
        "title": "t",
        "publication_date": "2024-01-01",
        "authorships": [
            {"author": {"id": "https://openalex.org/A1"}, "countries": []},
            {"author": {}, "countries": ["DE"]},
        ],
    }
    work_kw, auths = parse_work(rec)
    assert work_kw["n_authors"] == 2
    assert work_kw["is_intl_collab"] is True
    assert len(auths) == 1
