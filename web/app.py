from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Researcher, get_engine
from web import queries

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "db/researchers.db"


def _fmt(value):
    return "–" if value is None else f"{value:.2f}"


def _pct(value):
    return "–" if value is None else f"{value * 100:.0f}%"


def _man(value):
    return "–" if not value else f"{value // 10000:,}万円"


def _fmt_int(value):
    return "–" if value is None else f"{value:,}"


def _fmt_raw(value):
    return value if value else "–"


def _compare_table(pairs):
    """pairs: list[(Researcher, ResearcherMetrics|None)] → グループ/行/セル構造。
    最良値はサーバー側で計算する（数値のみ・None除外・全員同値なら無し）"""

    def metric(attr):
        return lambda r, m: getattr(m, attr) if m is not None else None

    def rattr(attr):
        return lambda r, m: getattr(r, attr)

    def roster_metric(attr):
        return lambda r, m: (getattr(m, attr)
                             if m is not None and r.is_official_roster
                             else None)

    spec = [
        ("基本", [
            ("主分野", metric("top_subfield"), _fmt_raw, False),
            ("h指数（全期間）", rattr("h_index"), _fmt_int, True),
            ("i10指数（全期間）", rattr("i10_index"), _fmt_int, True),
        ]),
        ("生産性", [
            ("3年論文数", metric("works_count_3y"), _fmt_int, True),
            ("論文数(補正)", metric("fractional_works"), _fmt, True),
            ("筆頭著者数", metric("first_author_count"), _fmt_int, True),
            ("責任著者数", metric("corresponding_count"), _fmt_int, True),
        ]),
        ("インパクト", [
            ("総被引用数", metric("total_citations"), _fmt_int, True),
            ("被引用(補正)", metric("fractional_citations"), _fmt, True),
            ("FWCI合計", metric("fwci_total"), _fmt, True),
            ("FWCI平均", metric("fwci_mean"), _fmt, True),
            ("FWCI中央値", metric("fwci_median"), _fmt, True),
            ("top10%論文数", metric("top10pct_count"), _fmt_int, True),
            ("top1%論文数", metric("top1pct_count"), _fmt_int, True),
        ]),
        ("連携・資金", [
            ("国際共著率", metric("intl_collab_rate"), _pct, True),
            ("産学連携率", metric("corp_collab_rate"), _pct, True),
            ("OA率", metric("oa_rate"), _pct, True),
            ("科研費（代表）", metric("kaken_pi_count"), _fmt_int, True),
            ("科研費（分担）", metric("kaken_copi_count"), _fmt_int, True),
            ("科研費配分総額", metric("kaken_total_amount"), _man, True),
        ]),
        ("実績（全期間・公式総覧）", [
            ("受賞数", roster_metric("awards_count"), _fmt_int, True),
            ("著書数", roster_metric("books_count"), _fmt_int, True),
            ("講演数", roster_metric("presentations_count"), _fmt_int, True),
            ("委員歴数", roster_metric("committee_count"), _fmt_int, True),
        ]),
    ]
    groups = []
    for group_label, rows_spec in spec:
        rows = []
        for label, getter, formatter, highlight in rows_spec:
            values = [getter(r, m) for r, m in pairs]
            numeric = [v for v in values if isinstance(v, (int, float))]
            best = None
            if highlight and len(numeric) >= 2 and len(set(numeric)) > 1:
                best = max(numeric)
            rows.append({
                "label": label,
                "cells": [{"text": formatter(v),
                           "best": best is not None and v == best}
                          for v in values],
            })
        groups.append({"label": group_label, "rows": rows})
    return groups


MAX_PARAM = 1_000_000


def _int_param(value, default, minimum=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= MAX_PARAM else default


def _bar_widths(rows, sort_key):
    """ソート中の指標の値を、ページ内最大値比のバー幅%（2..100）に変換する"""
    values = {}
    for row in rows:
        v = getattr(row.ResearcherMetrics, sort_key, None)
        if isinstance(v, (int, float)) and v > 0:
            values[row.Researcher.openalex_id] = v
    if not values:
        return {}
    mx = max(values.values())
    return {rid: max(2, round(v / mx * 100)) for rid, v in values.items()}


def create_app(db_path: str = DEFAULT_DB) -> FastAPI:
    if not Path(db_path).exists():
        raise RuntimeError(
            f"DBファイルがありません: {db_path} — "
            "先に `uv run python scripts/sync.py` を実行してください")
    engine = get_engine(db_path)
    app = FastAPI(title="OMU at a glance", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"),
              name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["fmt"] = _fmt
    templates.env.filters["pct"] = _pct
    templates.env.filters["man"] = _man

    @app.get("/", response_class=HTMLResponse)
    def ranking_page(request: Request, sort: str = "fwci_total",
                     min_works: str = "1", page: str = "1",
                     department: str = ""):
        sort_key = sort if sort in queries.SORT_COLUMNS else "fwci_total"
        mw = _int_param(min_works, 1)
        pg = _int_param(page, 1, minimum=1)
        with Session(engine) as session:
            departments = queries.departments_list(session)
            dept = department if department in departments else None
            rows, total, total_all = queries.ranking(
                session, sort_key, mw, pg, department=dept)
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "ranking.html", {
            "rows": rows, "total": total, "total_all": total_all,
            "sort": sort_key, "min_works": mw, "page": pg,
            "page_size": queries.PAGE_SIZE, "synced": synced,
            "departments": departments, "department": dept,
            "bars": _bar_widths(rows, sort_key),
        })

    @app.get("/researchers/{openalex_id}", response_class=HTMLResponse)
    def researcher_page(request: Request, openalex_id: str):
        with Session(engine) as session:
            researcher = session.get(Researcher, openalex_id)
            if researcher is not None and researcher.canonical_id:
                return RedirectResponse(
                    f"/researchers/{researcher.canonical_id}", status_code=302)
            result = queries.researcher_detail(session, openalex_id)
            ranks = queries.metric_ranks(session, openalex_id)
            awards = queries.awards_for(session, openalex_id)
            synced = queries.last_synced(session)
        if result is None:
            return templates.TemplateResponse(
                request, "404.html", {"synced": synced}, status_code=404)
        researcher, metrics, works = result
        return templates.TemplateResponse(request, "researcher.html", {
            "r": researcher, "m": metrics, "works": works, "ranks": ranks or {}, "awards": awards, "synced": synced,
        })

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, q: str = ""):
        q = q.strip()
        rows = []
        with Session(engine) as session:
            synced = queries.last_synced(session)
            if q:
                rows = queries.search(session, q)
        return templates.TemplateResponse(request, "search.html", {
            "q": q, "rows": rows, "synced": synced,
        })

    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request, ids: str = ""):
        id_list: list[str] = []
        for raw_id in ids.split(","):
            rid = raw_id.strip()
            if rid and rid not in id_list:
                id_list.append(rid)
            if len(id_list) >= 50:
                break
        with Session(engine) as session:
            synced = queries.last_synced(session)
            entries = queries.compare(session, id_list)[:4]
        if len(entries) < 2:
            return templates.TemplateResponse(request, "compare.html", {
                "pairs": [], "groups": [], "subfield_warning": False,
                "synced": synced,
            })
        pairs = [(row.Researcher, row.ResearcherMetrics) for row in entries]
        subfields = {m.top_subfield for r, m in pairs
                     if m is not None and m.top_subfield}
        return templates.TemplateResponse(request, "compare.html", {
            "pairs": pairs, "groups": _compare_table(pairs),
            "subfield_warning": len(subfields) > 1, "synced": synced,
        })

    @app.get("/departments", response_class=HTMLResponse)
    def departments_page(request: Request):
        with Session(engine) as session:
            ranked, small = queries.department_stats(session)
            matched = (sum(item["members"] for item in ranked)
                       + sum(item["members"] for item in small))
            total_count = session.scalar(
                select(func.count()).select_from(Researcher)
                .where(Researcher.canonical_id.is_(None)))
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "departments.html", {
            "ranked": ranked, "small": small, "matched": matched,
            "total_count": total_count, "synced": synced,
            "min_members": queries.MIN_DEPT_MEMBERS,
        })

    return app


def create_default_app() -> FastAPI:
    return create_app(DEFAULT_DB)
