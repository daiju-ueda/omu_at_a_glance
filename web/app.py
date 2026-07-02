from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db.models import get_engine
from web import queries

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = "db/researchers.db"


def _fmt(value):
    return "–" if value is None else f"{value:.2f}"


def _pct(value):
    return "–" if value is None else f"{value * 100:.0f}%"


MAX_PARAM = 1_000_000


def _int_param(value, default, minimum=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= MAX_PARAM else default


def create_app(db_path: str = DEFAULT_DB) -> FastAPI:
    if not Path(db_path).exists():
        raise RuntimeError(
            f"DBファイルがありません: {db_path} — "
            "先に `uv run python scripts/sync.py` を実行してください")
    engine = get_engine(db_path)
    app = FastAPI(title="OMU研究者比較", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"),
              name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["fmt"] = _fmt
    templates.env.filters["pct"] = _pct

    @app.get("/", response_class=HTMLResponse)
    def ranking_page(request: Request, sort: str = "fwci_mean",
                     min_works: str = "1", page: str = "1"):
        sort_key = sort if sort in queries.SORT_COLUMNS else "fwci_mean"
        mw = _int_param(min_works, 1)
        pg = _int_param(page, 1, minimum=1)
        with Session(engine) as session:
            rows, total, total_all = queries.ranking(session, sort_key, mw, pg)
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "ranking.html", {
            "rows": rows, "total": total, "total_all": total_all,
            "sort": sort_key, "min_works": mw, "page": pg,
            "page_size": queries.PAGE_SIZE, "synced": synced,
        })

    @app.get("/researchers/{openalex_id}", response_class=HTMLResponse)
    def researcher_page(request: Request, openalex_id: str):
        with Session(engine) as session:
            result = queries.researcher_detail(session, openalex_id)
            synced = queries.last_synced(session)
        if result is None:
            return templates.TemplateResponse(
                request, "404.html", {"synced": synced}, status_code=404)
        researcher, metrics, works = result
        return templates.TemplateResponse(request, "researcher.html", {
            "r": researcher, "m": metrics, "works": works, "synced": synced,
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

    return app


def create_default_app() -> FastAPI:
    return create_app(DEFAULT_DB)
