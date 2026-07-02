# 閲覧Web MVP 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `db/researchers.db` の研究者比較データをFastAPI+Jinja2の3ページ（ランキング/研究者詳細/検索）でLAN/Tailscaleからブラウザ閲覧できるようにする。

**Architecture:** `web/queries.py` が読み取り専用クエリ、`web/app.py` がFastAPIアプリファクトリ＋3ルート、Jinja2テンプレート＋CSS1枚。uvicornをポート8100で配信、systemd unitはファイル同梱＋README手順（sudoが要るため自動インストールしない）。

**Tech Stack:** FastAPI 0.115.6 / uvicorn 0.34.0 / Jinja2 3.1.5（MedAIDigestと同じピン）、既存のSQLAlchemyモデルを再利用

**設計書:** `docs/superpowers/specs/2026-07-02-web-viewer-design.md`

## Global Constraints

- 読み取り専用（DBへの書き込みコードを一切書かない）
- デフォルトソートは `fwci_mean`（生被引用数ではない）。ソート可能列は `fwci_mean` / `total_citations` / `top10pct_count` / `works_count_3y` の4つのみ（ホワイトリスト方式、不正値はデフォルトにフォールバック）
- `min_works` デフォルト5、`page` は1始まり100件/ページ。不正なクエリパラメタは500を出さずデフォルトへフォールバック
- 全ページのフッターに「OpenAlex収録分に基づく（直近3年ローリング）・最終同期: <sync_stateのworks last_synced_at>」
- 氏名表示は常に `name_ja or display_name`（Phase 2対応）、検索も `display_name` と `name_ja` の両方を対象
- FWCI等の欠損は「–」、数値は小数2桁（Jinjaフィルタ `fmt`）
- 研究者詳細の論文リストは `collector.sync.window_start(今日)` 以降のみ（DBにはウィンドウ外に出た過去worksが残留するため必ずフィルタ）
- ポート8100、`uvicorn web.app:create_default_app --factory`（モジュールimport時にDBを開かない）
- テストは実DBに依存しない（tmp_pathのSQLiteをseed）。実APIアクセスなし

---

### Task 1: 読み取りクエリ層（web/queries.py）

**Files:**
- Create: `web/__init__.py`（空）
- Create: `web/queries.py`
- Create: `tests/conftest.py`（seed fixture、Task 2のテストも使う）
- Test: `tests/test_web_queries.py`

**Interfaces:**
- Consumes: `db.models`（Researcher, ResearcherMetrics, Work, Authorship, SyncState, get_engine）、`collector.sync.window_start`
- Produces:
  - `web.queries.PAGE_SIZE = 100`
  - `web.queries.SORT_COLUMNS: dict[str, Column]` — キーは `"fwci_mean" | "total_citations" | "top10pct_count" | "works_count_3y"`
  - `web.queries.ranking(session, sort="fwci_mean", min_works=5, page=1) -> tuple[list[Row], int]` — Rowは `.Researcher` / `.ResearcherMetrics` を持つ。降順、同値はopenalex_id昇順、返り値2つ目はフィルタ後の総件数
  - `web.queries.researcher_detail(session, openalex_id, today=None) -> tuple[Researcher, ResearcherMetrics|None, list[Row]] | None` — Rowは `.Work` / `.Authorship`。被引用数降順、ウィンドウ内のみ
  - `web.queries.search(session, q, limit=200) -> list[Row]` — display_name/name_ja の大文字小文字無視部分一致
  - `web.queries.last_synced(session) -> str | None`

- [ ] **Step 1: conftest.py を書く**（`tests/conftest.py`）

```python
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
```

- [ ] **Step 2: 失敗するテストを書く**（`tests/test_web_queries.py`）

```python
from sqlalchemy.orm import Session

from db.models import get_engine
from web.queries import last_synced, ranking, researcher_detail, search


def _session(path):
    return Session(get_engine(path))


def test_ranking_default_filters_and_sorts(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s)
    ids = [r.Researcher.openalex_id for r in rows]
    assert ids == ["A1", "A2"]  # A3はworks<5で除外、NULL FWCIは末尾
    assert total == 2


def test_ranking_min_works_zero_and_sort_switch(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s, min_works=0)
        assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1", "A2"]
        assert total == 3
        rows, _ = ranking(s, sort="total_citations", min_works=0)
        assert [r.Researcher.openalex_id for r in rows][0] == "A2"


def test_ranking_invalid_sort_falls_back(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, _ = ranking(s, sort="evil'; DROP TABLE works;--", min_works=0)
    assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1", "A2"]


def test_ranking_pagination(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total = ranking(s, min_works=0, page=2)
    assert rows == [] and total == 3


def test_researcher_detail(seeded_db_path):
    with _session(seeded_db_path) as s:
        result = researcher_detail(s, "A1")
        assert result is not None
        r, m, works = result
        assert r.display_name == "Taro Yamada"
        assert m.works_count_3y == 10
        # W2はウィンドウ外で除外、被引用数降順
        assert [row.Work.openalex_id for row in works] == ["W1", "W3"]
        assert researcher_detail(s, "NOPE") is None


def test_search(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = search(s, "yama")
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]
        assert search(s, "zzz") == []


def test_last_synced(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert last_synced(s) == "2026-07-02"
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web_queries.py -v`
Expected: FAIL（`ModuleNotFoundError: web` 等のimport error）

- [ ] **Step 4: 実装**（`web/__init__.py` は空ファイル、`web/queries.py`）

```python
import datetime

from sqlalchemy import func, or_, select

from collector.sync import window_start
from db.models import (Authorship, Researcher, ResearcherMetrics, SyncState,
                       Work)

PAGE_SIZE = 100

SORT_COLUMNS = {
    "fwci_mean": ResearcherMetrics.fwci_mean,
    "total_citations": ResearcherMetrics.total_citations,
    "top10pct_count": ResearcherMetrics.top10pct_count,
    "works_count_3y": ResearcherMetrics.works_count_3y,
}


def ranking(session, sort="fwci_mean", min_works=5, page=1):
    col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_mean)
    cond = ResearcherMetrics.works_count_3y >= min_works
    total = session.scalar(
        select(func.count()).select_from(ResearcherMetrics).where(cond))
    rows = session.execute(
        select(Researcher, ResearcherMetrics)
        .join(ResearcherMetrics,
              ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(cond)
        # SQLiteはNULLを最小値として扱うため、DESCでNULLは自然に末尾になる
        .order_by(col.desc(), Researcher.openalex_id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return rows, total


def researcher_detail(session, openalex_id, today=None):
    researcher = session.get(Researcher, openalex_id)
    if researcher is None:
        return None
    metrics = session.get(ResearcherMetrics, openalex_id)
    start = window_start(today or datetime.date.today())
    works = session.execute(
        select(Work, Authorship)
        .join(Authorship, Authorship.work_id == Work.openalex_id)
        .where(Authorship.author_id == openalex_id,
               Work.publication_date >= start)
        .order_by(Work.cited_by_count.desc(), Work.openalex_id)
    ).all()
    return researcher, metrics, works


def search(session, q, limit=200):
    pattern = f"%{q}%"
    return session.execute(
        select(Researcher, ResearcherMetrics)
        .outerjoin(ResearcherMetrics,
                   ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(or_(Researcher.display_name.ilike(pattern),
                   Researcher.name_ja.ilike(pattern)))
        .order_by(ResearcherMetrics.fwci_mean.desc(), Researcher.openalex_id)
        .limit(limit)
    ).all()


def last_synced(session):
    state = session.get(SyncState, "works")
    return state.last_synced_at if state else None
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_web_queries.py -v`
Expected: PASS (7 tests)。続けて `uv run pytest -m "not smoke"` で全件PASS

- [ ] **Step 6: Commit**

```bash
git add web/__init__.py web/queries.py tests/conftest.py tests/test_web_queries.py
git commit -m "feat: 閲覧Web用の読み取りクエリ層"
```

---

### Task 2: FastAPIアプリ＋テンプレート＋CSS

**Files:**
- Modify: `pyproject.toml`（dependencies に追加: `"fastapi==0.115.6"`, `"uvicorn==0.34.0"`, `"jinja2==3.1.5"`）
- Create: `web/app.py`
- Create: `web/templates/base.html`, `ranking.html`, `researcher.html`, `search.html`, `404.html`
- Create: `web/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `web.queries`（Task 1の全関数）、`db.models.get_engine`、`tests/conftest.py` の `seeded_db_path` fixture
- Produces:
  - `web.app.create_app(db_path: str = "db/researchers.db") -> FastAPI` — DBファイル不存在なら `RuntimeError`（fail fast）
  - `web.app.create_default_app() -> FastAPI` — uvicorn `--factory` 用
  - ルート: `GET /`（ランキング）, `GET /researchers/{openalex_id}`, `GET /search?q=`, `GET /static/*`

- [ ] **Step 1: 依存を追加して同期**

pyproject.toml の `dependencies` を以下に変更:

```toml
dependencies = [
    "httpx==0.28.1",
    "sqlalchemy==2.0.36",
    "fastapi==0.115.6",
    "uvicorn==0.34.0",
    "jinja2==3.1.5",
]
```

Run: `uv sync`
Expected: fastapi/uvicorn/jinja2 がインストールされる

- [ ] **Step 2: 失敗するテストを書く**（`tests/test_web.py`）

```python
import pytest
from fastapi.testclient import TestClient

from web.app import create_app


@pytest.fixture()
def client(seeded_db_path):
    return TestClient(create_app(seeded_db_path))


def test_ranking_page_default(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Taro Yamada" in body
    assert "Ichiro Tanaka" not in body  # works<5は既定で除外
    assert body.index("Taro Yamada") < body.index("Hanako Suzuki")  # NULL FWCI末尾
    assert "最終同期: 2026-07-02" in body
    assert "OpenAlex収録分に基づく" in body


def test_ranking_sort_and_min_works(client):
    body = client.get("/?sort=total_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")
    assert "Ichiro Tanaka" in body


def test_ranking_invalid_params_fall_back(client):
    resp = client.get("/?sort=bogus&min_works=abc&page=-5")
    assert resp.status_code == 200
    assert "Taro Yamada" in resp.text


def test_researcher_detail(client):
    body = client.get("/researchers/A1").text
    assert "Taro Yamada" in body
    assert "Deep Learning in Radiology" in body
    assert "Old Paper Outside Window" not in body  # ウィンドウ外
    assert "orcid.org/0000-0001-1111-1111" in body
    assert "top1%" in body
    assert "–" in body  # W3のFWCI欠損表示


def test_researcher_404(client):
    resp = client.get("/researchers/NOPE")
    assert resp.status_code == 404


def test_search(client):
    assert "Taro Yamada" in client.get("/search?q=yama").text
    assert "見つかりませんでした" in client.get("/search?q=zzzzz").text
    assert client.get("/search").status_code == 200


def test_missing_db_fails_fast(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path / "missing.db"))
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web.py -v`
Expected: FAIL（import error）

- [ ] **Step 4: アプリ実装**（`web/app.py`）

```python
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


def _int_param(value, default, minimum=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def create_app(db_path: str = DEFAULT_DB) -> FastAPI:
    if not Path(db_path).exists():
        raise RuntimeError(
            f"DBファイルがありません: {db_path} — "
            "先に `uv run python scripts/sync.py` を実行してください")
    engine = get_engine(db_path)
    app = FastAPI(title="OMU研究者比較")
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"),
              name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["fmt"] = _fmt

    @app.get("/", response_class=HTMLResponse)
    def ranking_page(request: Request, sort: str = "fwci_mean",
                     min_works: str = "5", page: str = "1"):
        sort_key = sort if sort in queries.SORT_COLUMNS else "fwci_mean"
        mw = _int_param(min_works, 5)
        pg = _int_param(page, 1, minimum=1)
        with Session(engine) as session:
            rows, total = queries.ranking(session, sort_key, mw, pg)
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "ranking.html", {
            "rows": rows, "total": total, "sort": sort_key,
            "min_works": mw, "page": pg, "page_size": queries.PAGE_SIZE,
            "synced": synced,
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
```

- [ ] **Step 5: テンプレート5枚を書く**

`web/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}OMU研究者比較{% endblock %}</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <h1><a href="/">OMU研究者比較</a></h1>
  <nav><a href="/">ランキング</a><a href="/search">検索</a></nav>
</header>
<main>
{% block content %}{% endblock %}
</main>
<footer>OpenAlex収録分に基づく（直近3年ローリング）{% if synced %}・最終同期: {{ synced }}{% endif %}</footer>
</body>
</html>
```

`web/templates/ranking.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>ランキング <span class="total">{{ total }}人</span></h2>
<form method="get" class="controls">
  <input type="hidden" name="sort" value="{{ sort }}">
  <label>最低論文数（3年）: <input type="number" name="min_works" value="{{ min_works }}" min="0"></label>
  <button>絞り込み</button>
</form>
<table>
<thead><tr>
  <th>#</th>
  <th>氏名</th>
  <th><a href="/?sort=works_count_3y&min_works={{ min_works }}">論文数{% if sort == 'works_count_3y' %} ▼{% endif %}</a></th>
  <th><a href="/?sort=total_citations&min_works={{ min_works }}">総被引用{% if sort == 'total_citations' %} ▼{% endif %}</a></th>
  <th><a href="/?sort=fwci_mean&min_works={{ min_works }}">FWCI平均{% if sort == 'fwci_mean' %} ▼{% endif %}</a></th>
  <th>FWCI中央値</th>
  <th><a href="/?sort=top10pct_count&min_works={{ min_works }}">top10%{% if sort == 'top10pct_count' %} ▼{% endif %}</a></th>
  <th>筆頭</th>
  <th>責任</th>
</tr></thead>
<tbody>
{% for row in rows %}
{% set r = row.Researcher %}{% set m = row.ResearcherMetrics %}
<tr>
  <td>{{ (page - 1) * page_size + loop.index }}</td>
  <td><a href="/researchers/{{ r.openalex_id }}">{{ r.name_ja or r.display_name }}</a></td>
  <td>{{ m.works_count_3y }}</td>
  <td>{{ m.total_citations }}</td>
  <td>{{ m.fwci_mean|fmt }}</td>
  <td>{{ m.fwci_median|fmt }}</td>
  <td>{{ m.top10pct_count }}</td>
  <td>{{ m.first_author_count }}</td>
  <td>{{ m.corresponding_count }}</td>
</tr>
{% endfor %}
</tbody>
</table>
<div class="pager">
{% if page > 1 %}<a href="/?sort={{ sort }}&min_works={{ min_works }}&page={{ page - 1 }}">← 前へ</a>{% endif %}
{% if page * page_size < total %}<a href="/?sort={{ sort }}&min_works={{ min_works }}&page={{ page + 1 }}">次へ →</a>{% endif %}
</div>
{% endblock %}
```

`web/templates/researcher.html`:

```html
{% extends "base.html" %}
{% block title %}{{ r.name_ja or r.display_name }} - OMU研究者比較{% endblock %}
{% block content %}
<h2>{{ r.name_ja or r.display_name }}</h2>
<p class="links">
  <a href="https://openalex.org/{{ r.openalex_id }}" target="_blank">OpenAlex</a>
  {% if r.orcid %}<a href="https://orcid.org/{{ r.orcid }}" target="_blank">ORCID</a>{% endif %}
</p>
{% if m %}
<dl class="metrics">
  <div><dt>3年論文数</dt><dd>{{ m.works_count_3y }}</dd></div>
  <div><dt>総被引用数</dt><dd>{{ m.total_citations }}</dd></div>
  <div><dt>FWCI平均</dt><dd>{{ m.fwci_mean|fmt }}</dd></div>
  <div><dt>FWCI中央値</dt><dd>{{ m.fwci_median|fmt }}</dd></div>
  <div><dt>top10%論文</dt><dd>{{ m.top10pct_count }}</dd></div>
  <div><dt>筆頭著者</dt><dd>{{ m.first_author_count }}</dd></div>
  <div><dt>責任著者</dt><dd>{{ m.corresponding_count }}</dd></div>
  <div><dt>h指数（全期間）</dt><dd>{{ r.h_index }}</dd></div>
</dl>
{% else %}
<p>メトリクス未計算です。</p>
{% endif %}
<h3>論文（直近3年・被引用数順）</h3>
<table>
<thead><tr><th>タイトル</th><th>掲載誌</th><th>発行日</th><th>被引用</th><th>FWCI</th><th>役割</th></tr></thead>
<tbody>
{% for row in works %}
{% set w = row.Work %}{% set a = row.Authorship %}
<tr>
  <td>
    {% if w.doi %}<a href="https://doi.org/{{ w.doi }}" target="_blank">{{ w.title }}</a>
    {% else %}<a href="https://openalex.org/{{ w.openalex_id }}" target="_blank">{{ w.title }}</a>{% endif %}
    {% if w.is_top1pct %}<span class="badge top1">top1%</span>
    {% elif w.is_top10pct %}<span class="badge top10">top10%</span>{% endif %}
  </td>
  <td>{{ w.venue or "–" }}</td>
  <td>{{ w.publication_date }}</td>
  <td>{{ w.cited_by_count }}</td>
  <td>{{ w.fwci|fmt }}</td>
  <td>{% if a.author_position == 'first' %}筆頭{% endif %}{% if a.is_corresponding %}{% if a.author_position == 'first' %}・{% endif %}責任{% endif %}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endblock %}
```

`web/templates/search.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>研究者検索</h2>
<form method="get" class="controls">
  <input type="text" name="q" value="{{ q }}" placeholder="氏名（部分一致）" autofocus>
  <button>検索</button>
</form>
{% if q %}
  {% if rows %}
  <table>
  <thead><tr><th>氏名</th><th>3年論文数</th><th>FWCI平均</th></tr></thead>
  <tbody>
  {% for row in rows %}
  {% set r = row.Researcher %}{% set m = row.ResearcherMetrics %}
  <tr>
    <td><a href="/researchers/{{ r.openalex_id }}">{{ r.name_ja or r.display_name }}</a></td>
    <td>{% if m %}{{ m.works_count_3y }}{% else %}–{% endif %}</td>
    <td>{% if m %}{{ m.fwci_mean|fmt }}{% else %}–{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
  </table>
  {% else %}
  <p>該当する研究者が見つかりませんでした。</p>
  {% endif %}
{% endif %}
{% endblock %}
```

`web/templates/404.html`:

```html
{% extends "base.html" %}
{% block content %}
<h2>見つかりません</h2>
<p>指定された研究者は存在しません。<a href="/">ランキングへ戻る</a></p>
{% endblock %}
```

- [ ] **Step 6: CSSを書く**（`web/static/style.css`）

```css
:root { --ink: #1a1a2e; --muted: #6b7280; --line: #e5e7eb; --accent: #0f4c81; --bg: #fafaf8; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Hiragino Sans", "Noto Sans JP", system-ui, sans-serif; color: var(--ink); background: var(--bg); font-size: 15px; line-height: 1.6; }
header { display: flex; align-items: baseline; gap: 1.5rem; padding: 0.9rem 1.5rem; background: var(--accent); }
header h1 { font-size: 1.1rem; margin: 0; }
header h1 a { color: #fff; text-decoration: none; }
header nav a { color: #cfe3f5; text-decoration: none; margin-right: 1rem; font-size: 0.9rem; }
header nav a:hover { color: #fff; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h2 { font-size: 1.25rem; }
h2 .total { font-size: 0.85rem; color: var(--muted); font-weight: normal; margin-left: 0.5rem; }
.controls { margin: 0.8rem 0 1.2rem; display: flex; gap: 0.6rem; align-items: center; }
.controls input[type="number"] { width: 5rem; }
.controls input, .controls button { padding: 0.3rem 0.6rem; border: 1px solid var(--line); border-radius: 4px; font-size: 0.9rem; }
.controls button { background: var(--accent); color: #fff; border-color: var(--accent); cursor: pointer; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: 0.9rem; }
th, td { padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--line); text-align: left; }
th { background: #f3f4f6; white-space: nowrap; }
th a { color: var(--accent); text-decoration: none; }
tbody tr:hover { background: #f0f6fb; }
td a { color: var(--accent); }
.badge { display: inline-block; font-size: 0.7rem; padding: 0.05rem 0.4rem; border-radius: 999px; margin-left: 0.4rem; vertical-align: middle; }
.badge.top1 { background: #7c2d12; color: #fff; }
.badge.top10 { background: #b45309; color: #fff; }
.links a { margin-right: 0.8rem; color: var(--accent); }
.metrics { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 0.6rem; margin: 1rem 0 1.5rem; }
.metrics div { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 0.6rem 0.8rem; }
.metrics dt { font-size: 0.75rem; color: var(--muted); }
.metrics dd { margin: 0; font-size: 1.2rem; font-weight: 600; }
.pager { margin: 1rem 0; display: flex; gap: 1rem; }
.pager a { color: var(--accent); }
footer { max-width: 1100px; margin: 1rem auto 2rem; padding: 0 1.5rem; color: var(--muted); font-size: 0.8rem; }
```

- [ ] **Step 7: テスト通過を確認**

Run: `uv run pytest tests/test_web.py -v`
Expected: PASS (8 tests)。続けて `uv run pytest -m "not smoke"` で全件PASS・警告なし

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock web tests/test_web.py
git commit -m "feat: 閲覧Web MVP（ランキング/研究者詳細/検索）"
```

---

### Task 3: systemd unit・README・実起動スモーク

**Files:**
- Create: `deploy/omu-researchers-web.service`
- Modify: `README.md`（「閲覧Web」セクション追加）

**Interfaces:**
- Consumes: Task 2の `create_default_app`
- Produces: 実DB（db/researchers.db）で起動確認済みのWebサービスと運用手順

- [ ] **Step 1: systemd unit を書く**（`deploy/omu-researchers-web.service`）

```ini
[Unit]
Description=OMU Researchers Web Viewer
After=network.target

[Service]
Type=simple
User=d-ueda
WorkingDirectory=/srv/apps/researchers
ExecStart=/home/d-ueda/.local/bin/uv run uvicorn web.app:create_default_app --factory --host 0.0.0.0 --port 8100
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: README に「閲覧Web」セクションを追加**

「## cron（推奨）」セクションの前に挿入:

```markdown
## 閲覧Web

    uv run uvicorn web.app:create_default_app --factory --host 0.0.0.0 --port 8100

LAN/Tailscale の IP で `http://<host>:8100/` を開く。ページ: `/`（ランキング）・`/search`（検索）・`/researchers/<id>`（詳細）。

常駐させる場合（systemd、要sudo）:

    sudo cp deploy/omu-researchers-web.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now omu-researchers-web
```

- [ ] **Step 3: 実DBで起動スモーク**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8100 &
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8100/            # 200
curl -s http://127.0.0.1:8100/ | grep -c "researchers/A"                   # 1以上
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8100/search?q=ueda"  # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8100/researchers/NOPE # 404
kill %1
```

Expected: 200 / 1以上 / 200 / 404。実データ（3,876人）のランキングがHTMLで返ること

- [ ] **Step 4: 全テスト確認とCommit**

Run: `uv run pytest -m "not smoke"`
Expected: 全件PASS

```bash
git add deploy/omu-researchers-web.service README.md
git commit -m "feat: systemd unitと閲覧Webの運用手順"
```

---

## 完了条件

- `uv run pytest -m "not smoke"` 全件PASS
- 実DBで `/` `/search` `/researchers/<実ID>` が正しく表示され、存在しないIDは404
- README の手順どおりに起動でき、フッターに最終同期日が出る
