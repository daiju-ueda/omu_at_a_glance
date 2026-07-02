import json


def strip_id(url: str | None) -> str | None:
    if url is None:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1]


def _dumps(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False)


def parse_author(rec: dict) -> dict:
    return {
        "openalex_id": strip_id(rec["id"]),
        "display_name": rec.get("display_name") or "",
        "orcid": strip_id(rec.get("orcid")),
        "h_index": (rec.get("summary_stats") or {}).get("h_index") or 0,
        "works_count": rec.get("works_count") or 0,
        "i10_index": (rec.get("summary_stats") or {}).get("i10_index") or 0,
        "two_yr_mean_citedness": (rec.get("summary_stats") or {}).get(
            "2yr_mean_citedness"),
        "raw_json": _dumps(rec),
        "updated_at": rec.get("updated_date") or "",
    }


def parse_work(rec: dict) -> tuple[dict, list[dict]]:
    auth_list = rec.get("authorships") or []
    countries = {c for a in auth_list for c in (a.get("countries") or [])}
    has_corp = any(
        inst.get("type") == "company"
        for a in auth_list for inst in (a.get("institutions") or []))
    cnp = rec.get("citation_normalized_percentile") or {}
    topic = rec.get("primary_topic") or {}
    source = (rec.get("primary_location") or {}).get("source") or {}
    work_id = strip_id(rec["id"])
    doi = rec.get("doi")
    work_kw = {
        "openalex_id": work_id,
        "doi": doi.removeprefix("https://doi.org/") if doi else None,
        "title": rec.get("title") or "",
        "publication_date": rec.get("publication_date") or "",
        "venue": source.get("display_name"),
        "type": rec.get("type"),
        "cited_by_count": rec.get("cited_by_count") or 0,
        "fwci": rec.get("fwci"),
        "cnp_value": cnp.get("value"),
        "is_top1pct": bool(cnp.get("is_in_top_1_percent", False)),
        "is_top10pct": bool(cnp.get("is_in_top_10_percent", False)),
        "topic": topic.get("display_name"),
        "subfield": (topic.get("subfield") or {}).get("display_name"),
        "is_oa": bool((rec.get("open_access") or {}).get("is_oa", False)),
        "n_authors": len(auth_list),
        "is_intl_collab": bool(countries - {"JP"}),
        "is_corp_collab": has_corp,
        "raw_json": _dumps(rec),
        "updated_at": rec.get("updated_date") or "",
    }
    authorships = [
        {
            "work_id": work_id,
            "author_id": strip_id((a.get("author") or {}).get("id")),
            "author_position": a.get("author_position"),
            "is_corresponding": bool(a.get("is_corresponding", False)),
        }
        for a in auth_list
        if (a.get("author") or {}).get("id")
    ]
    return work_kw, authorships
