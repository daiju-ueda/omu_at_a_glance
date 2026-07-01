# Phase 1: OpenAlex MVP 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 大阪公立大学の研究者（約3,900人）と直近3年の論文・被引用データ（約6,200件）をOpenAlex APIからSQLiteに同期し、研究者別の比較指標（FWCI等）を計算できる状態にする。

**Architecture:** `collector/openalex.py` がcursor paging＋リトライ付きAPIクライアント、`collector/sync.py` がparse＋upsert＋差分同期、`collector/metrics.py` が研究者別集計。`scripts/` はCLIの薄いラッパー。DBはSQLAlchemy 2.x＋SQLite。

**Tech Stack:** Python (uv), httpx, SQLAlchemy 2.0, pytest

## Global Constraints

- Python `>=3.10,<3.13`（MedAIDigestと同じ）
- 無料ソースのみ。OpenAlexは全リクエストに `mailto=ai.labo.ocu@gmail.com` を付与（polite pool）
- 機関IDはパラメタ化し、デフォルト `I4387152983`（大阪公立大学）
- DBファイルは `db/researchers.db`（gitignore対象）
- 取得レコードは常に `raw_json` 列に保存（再取得なしでスキーマ変更可能にする）
- OpenAlexのIDはプレフィックスを剥がして保存（`https://openalex.org/A123` → `A123`、ORCIDも同様）
- 論文ウィンドウは「実行日から3年前」のローリング。集計側も同じウィンドウを使う

---

### Task 1: プロジェクト雛形

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `collector/__init__.py`, `tests/__init__.py`, `scripts/__init__.py`, `db/__init__.py`（全て空ファイル）

**Interfaces:**
- Produces: `uv run pytest` が動く環境。パッケージ `collector`, `db` がimport可能

- [ ] **Step 1: pyproject.toml を書く**

```toml
[project]
name = "omu-researchers"
version = "0.1.0"
description = "OMU researcher & publication data pipeline (OpenAlex)"
requires-python = ">=3.10,<3.13"
dependencies = [
    "httpx==0.28.1",
    "sqlalchemy==2.0.36",
]

[dependency-groups]
dev = [
    "pytest==8.3.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["smoke: hits the real OpenAlex API (deselect with '-m \"not smoke\"')"]
```

- [ ] **Step 2: .gitignore を書く**

```gitignore
__pycache__/
*.py[cod]
.venv/
db/researchers.db
.pytest_cache/
```

- [ ] **Step 3: 空の `__init__.py` を4つ作成**

```bash
touch collector/__init__.py tests/__init__.py scripts/__init__.py db/__init__.py
```

- [ ] **Step 4: 環境構築と確認**

Run: `uv sync && uv run pytest`
Expected: `no tests ran`（exit code 5でよい）

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore collector tests scripts db
git commit -m "chore: uv プロジェクト雛形"
```

---

### Task 2: DBモデル

**Files:**
- Create: `db/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `db.models.Base`（DeclarativeBase）
  - `db.models.Researcher(openalex_id: str [PK], display_name: str, orcid: str|None, h_index: int, works_count: int, name_ja: str|None, department: str|None, position: str|None, is_official_roster: bool, raw_json: str, updated_at: str)`
  - `db.models.Work(openalex_id: str [PK], doi: str|None, title: str, publication_date: str, venue: str|None, type: str|None, cited_by_count: int, fwci: float|None, cnp_value: float|None, is_top1pct: bool, is_top10pct: bool, topic: str|None, subfield: str|None, is_oa: bool, raw_json: str, updated_at: str)`
  - `db.models.Authorship(work_id: str [PK], author_id: str [PK], author_position: str|None, is_corresponding: bool)` — `author_id` は Researcher.openalex_id と同じID空間。OMU外の共著者も行を持つ（researchersに対応行が無いだけ）ので、外部キー制約は張らない
  - `db.models.ResearcherMetrics(researcher_id: str [PK], works_count_3y: int, total_citations: int, fwci_mean: float|None, fwci_median: float|None, top10pct_count: int, first_author_count: int, corresponding_count: int, computed_at: str)`
  - `db.models.SyncState(source: str [PK], cursor: str|None, last_synced_at: str|None)`
  - `db.models.get_engine(path: str = "db/researchers.db")` — テーブル作成込みでEngineを返す。`path=":memory:"` 対応

- [ ] **Step 1: 失敗するテストを書く** (`tests/test_models.py`)

```python
from sqlalchemy.orm import Session

from db.models import Researcher, Work, Authorship, get_engine


def test_roundtrip_in_memory():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(
            openalex_id="A123", display_name="Taro Yamada", orcid="0000-0001-2345-6789",
            h_index=10, works_count=50, raw_json="{}", updated_at="2026-07-02",
        ))
        s.add(Work(
            openalex_id="W456", doi="10.1000/x", title="t", publication_date="2024-01-01",
            venue="J", type="article", cited_by_count=3, fwci=1.5, cnp_value=0.9,
            is_top1pct=False, is_top10pct=True, topic="AI", subfield="ML",
            is_oa=True, raw_json="{}", updated_at="2026-07-02",
        ))
        s.add(Authorship(work_id="W456", author_id="A123",
                         author_position="first", is_corresponding=True))
        s.commit()
        assert s.get(Researcher, "A123").display_name == "Taro Yamada"
        assert s.get(Work, "W456").is_top10pct is True
        assert s.get(Authorship, ("W456", "A123")).author_position == "first"
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL（`ModuleNotFoundError` または import error）

- [ ] **Step 3: 実装** (`db/models.py`)

```python
from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Researcher(Base):
    __tablename__ = "researchers"
    openalex_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    orcid: Mapped[str | None] = mapped_column(String, nullable=True)
    h_index: Mapped[int] = mapped_column(Integer, default=0)
    works_count: Mapped[int] = mapped_column(Integer, default=0)
    # Phase 2 (公式名簿) で埋める列。Phase 1 では NULL のまま
    name_ja: Mapped[str | None] = mapped_column(String, nullable=True)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    is_official_roster: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class Work(Base):
    __tablename__ = "works"
    openalex_id: Mapped[str] = mapped_column(String, primary_key=True)
    doi: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[str] = mapped_column(String, index=True)
    venue: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0)
    fwci: Mapped[float | None] = mapped_column(Float, nullable=True)
    cnp_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_top1pct: Mapped[bool] = mapped_column(Boolean, default=False)
    is_top10pct: Mapped[bool] = mapped_column(Boolean, default=False)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    subfield: Mapped[str | None] = mapped_column(String, nullable=True)
    is_oa: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class Authorship(Base):
    __tablename__ = "authorships"
    work_id: Mapped[str] = mapped_column(String, primary_key=True)
    author_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    author_position: Mapped[str | None] = mapped_column(String, nullable=True)
    is_corresponding: Mapped[bool] = mapped_column(Boolean, default=False)


class ResearcherMetrics(Base):
    __tablename__ = "researcher_metrics"
    researcher_id: Mapped[str] = mapped_column(String, primary_key=True)
    works_count_3y: Mapped[int] = mapped_column(Integer, default=0)
    total_citations: Mapped[int] = mapped_column(Integer, default=0)
    fwci_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    fwci_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    top10pct_count: Mapped[int] = mapped_column(Integer, default=0)
    first_author_count: Mapped[int] = mapped_column(Integer, default=0)
    corresponding_count: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[str] = mapped_column(String)


class SyncState(Base):
    __tablename__ = "sync_state"
    source: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String, nullable=True)


def get_engine(path: str = "db/researchers.db"):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return engine
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/models.py tests/test_models.py
git commit -m "feat: SQLAlchemyモデル（researchers/works/authorships/metrics/sync_state）"
```

---

### Task 3: OpenAlex APIクライアント（cursor paging＋リトライ）

**Files:**
- Create: `collector/openalex.py`
- Test: `tests/test_openalex.py`

**Interfaces:**
- Produces:
  - `collector.openalex.OpenAlexClient(mailto: str = "ai.labo.ocu@gmail.com", transport: httpx.BaseTransport | None = None, sleep_fn=time.sleep)`
  - `.paginate(endpoint: str, filter_str: str, select: str | None = None, per_page: int = 200) -> Iterator[dict]` — cursor pagingで全レコードをyield
  - `.count(endpoint: str, filter_str: str) -> int` — `meta.count` を返す
  - 429/5xx は指数バックオフ（1,2,4,8,16秒）で最大6回試行、それでも失敗なら例外
  - `endpoint` は `"authors"` / `"works"` のようなパス断片

- [ ] **Step 1: 失敗するテストを書く** (`tests/test_openalex.py`)

```python
import json

import httpx
import pytest

from collector.openalex import OpenAlexClient

PAGE1 = {"meta": {"count": 3, "next_cursor": "CUR2"},
         "results": [{"id": "https://openalex.org/A1"}, {"id": "https://openalex.org/A2"}]}
PAGE2 = {"meta": {"count": 3, "next_cursor": None},
         "results": [{"id": "https://openalex.org/A3"}]}


def make_client(handler):
    return OpenAlexClient(transport=httpx.MockTransport(handler), sleep_fn=lambda s: None)


def test_paginate_follows_cursor_and_sends_mailto():
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=PAGE2 if cursor == "CUR2" else PAGE1)

    client = make_client(handler)
    rows = list(client.paginate("authors", "last_known_institutions.id:I999"))
    assert [r["id"] for r in rows] == [
        "https://openalex.org/A1", "https://openalex.org/A2", "https://openalex.org/A3"]
    assert seen_params[0]["cursor"] == "*"
    assert seen_params[0]["mailto"] == "ai.labo.ocu@gmail.com"
    assert seen_params[1]["cursor"] == "CUR2"


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json=PAGE2)

    client = make_client(handler)
    rows = list(client.paginate("works", "f:x"))
    assert len(rows) == 1
    assert calls["n"] == 3


def test_gives_up_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        list(client.paginate("works", "f:x"))


def test_count():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["per-page"] == "1"
        return httpx.Response(200, json={"meta": {"count": 6222}, "results": []})

    client = make_client(handler)
    assert client.count("works", "f:x") == 6222
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_openalex.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装** (`collector/openalex.py`)

```python
import logging
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6


class OpenAlexClient:
    def __init__(self, mailto: str = "ai.labo.ocu@gmail.com",
                 transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._mailto = mailto
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport)

    def _get(self, endpoint: str, params: dict) -> dict:
        params = {**params, "mailto": self._mailto}
        for attempt in range(MAX_TRIES):
            resp = self._http.get(f"/{endpoint}", params=params)
            if resp.status_code in RETRY_STATUSES:
                wait = 2 ** attempt
                logger.warning("OpenAlex %s -> %s, retry in %ss",
                               endpoint, resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()  # 最終試行のエラーを送出
        raise RuntimeError("unreachable")

    def paginate(self, endpoint: str, filter_str: str,
                 select: str | None = None, per_page: int = 200) -> Iterator[dict]:
        cursor = "*"
        while cursor:
            params = {"filter": filter_str, "per-page": per_page, "cursor": cursor}
            if select:
                params["select"] = select
            data = self._get(endpoint, params)
            yield from data["results"]
            cursor = data["meta"].get("next_cursor")

    def count(self, endpoint: str, filter_str: str) -> int:
        data = self._get(endpoint, {"filter": filter_str, "per-page": 1})
        return data["meta"]["count"]
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_openalex.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add collector/openalex.py tests/test_openalex.py
git commit -m "feat: OpenAlexクライアント（cursor paging・指数バックオフ・mailto）"
```

---

### Task 4: レコードのパース（author / work / authorships）

**Files:**
- Create: `collector/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: なし（純関数）
- Produces:
  - `collector.parse.strip_id(url: str | None) -> str | None` — `"https://openalex.org/A1"` → `"A1"`、`"https://orcid.org/0000-..."` → `"0000-..."`、None→None
  - `collector.parse.parse_author(rec: dict) -> dict` — `Researcher` のコンストラクタkwargs（`name_ja`等Phase 2列とis_official_rosterは含まない）
  - `collector.parse.parse_work(rec: dict) -> tuple[dict, list[dict]]` — `(Workのkwargs, Authorshipのkwargsのリスト)`。authorshipsは**全著者**を含む
  - どちらも `raw_json` に `json.dumps(rec, ensure_ascii=False)` を入れる。欠損フィールド（fwci=None、citation_normalized_percentile=None、primary_topic=None、primary_location=None、orcid=None等）で落ちないこと

- [ ] **Step 1: 失敗するテストを書く** (`tests/test_parse.py`)

実APIレスポンス（2026-07-02実測）を縮小したfixtureを使う:

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_parse.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装** (`collector/parse.py`)

```python
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
        "h_index": (rec.get("summary_stats") or {}).get("h_index", 0),
        "works_count": rec.get("works_count", 0),
        "raw_json": _dumps(rec),
        "updated_at": rec.get("updated_date") or "",
    }


def parse_work(rec: dict) -> tuple[dict, list[dict]]:
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
        "cited_by_count": rec.get("cited_by_count", 0),
        "fwci": rec.get("fwci"),
        "cnp_value": cnp.get("value"),
        "is_top1pct": bool(cnp.get("is_in_top_1_percent", False)),
        "is_top10pct": bool(cnp.get("is_in_top_10_percent", False)),
        "topic": topic.get("display_name"),
        "subfield": (topic.get("subfield") or {}).get("display_name"),
        "is_oa": bool((rec.get("open_access") or {}).get("is_oa", False)),
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
        for a in rec.get("authorships") or []
        if (a.get("author") or {}).get("id")
    ]
    return work_kw, authorships
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_parse.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add collector/parse.py tests/test_parse.py
git commit -m "feat: OpenAlexレコードのパーサ（author/work/authorships）"
```

---

### Task 5: 同期オーケストレーション（full / incremental・件数突合）

**Files:**
- Create: `collector/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `OpenAlexClient.paginate/count`（Task 3）、`parse_author/parse_work`（Task 4）、モデル（Task 2）
- Produces:
  - `collector.sync.INSTITUTION_ID = "I4387152983"`
  - `collector.sync.AUTHOR_SELECT` / `collector.sync.WORK_SELECT` — selectパラメタ文字列
  - `collector.sync.window_start(today: datetime.date) -> str` — 3年前のISO日付
  - `collector.sync.sync_authors(session, client, today: datetime.date, institution_id=INSTITUTION_ID, since: str | None = None) -> int` — upsert件数を返す。`since` 指定時は `from_updated_date:{since}` を付ける（incremental）
  - `collector.sync.sync_works(session, client, today: datetime.date, institution_id=INSTITUTION_ID, since: str | None = None) -> int` — works＋authorshipsをupsert。full時（since=None）は件数を `client.count` と突合し、不一致なら `logger.warning`
  - 各関数は `sync_state`（source="authors"/"works"）の `last_synced_at` に `today.isoformat()` を保存
  - upsertはSQLiteの `INSERT ... ON CONFLICT DO UPDATE`（`sqlalchemy.dialects.sqlite.insert`）で行い、1000件ごとにコミット

- [ ] **Step 1: 失敗するテストを書く** (`tests/test_sync.py`)

```python
import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from collector.sync import sync_authors, sync_works, window_start
from db.models import Authorship, Researcher, SyncState, Work, get_engine
from tests.test_parse import AUTHOR, WORK


class FakeClient:
    def __init__(self, results, count_value=None):
        self.results = results
        self.count_value = count_value
        self.calls = []

    def paginate(self, endpoint, filter_str, select=None, per_page=200):
        self.calls.append((endpoint, filter_str))
        yield from self.results

    def count(self, endpoint, filter_str):
        return self.count_value


TODAY = datetime.date(2026, 7, 2)


def test_window_start():
    assert window_start(TODAY) == "2023-07-02"


def test_sync_authors_upserts_and_records_state():
    engine = get_engine(":memory:")
    client = FakeClient([AUTHOR])
    with Session(engine) as s:
        n = sync_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A5023888391").h_index == 25
        assert s.get(SyncState, "authors").last_synced_at == "2026-07-02"
        # 再実行しても重複せず更新になる
        sync_authors(s, client, today=TODAY)
        assert s.scalar(select(func.count()).select_from(Researcher)) == 1
    assert "last_known_institutions.id:I4387152983" in client.calls[0][1]


def test_sync_authors_incremental_adds_updated_filter():
    engine = get_engine(":memory:")
    client = FakeClient([])
    with Session(engine) as s:
        sync_authors(s, client, today=TODAY, since="2026-06-25")
    assert "from_updated_date:2026-06-25" in client.calls[0][1]


def test_sync_works_upserts_works_and_authorships():
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=1)
    with Session(engine) as s:
        n = sync_works(s, client, today=TODAY)
        assert n == 1
        assert s.get(Work, "W4385564466").cited_by_count == 521
        assert s.scalar(select(func.count()).select_from(Authorship)) == 2
    assert "from_publication_date:2023-07-02" in client.calls[0][1]


def test_sync_works_full_warns_on_count_mismatch(caplog):
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=99)
    with Session(engine) as s:
        with caplog.at_level("WARNING"):
            sync_works(s, client, today=TODAY)
    assert any("mismatch" in r.message for r in caplog.records)
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装** (`collector/sync.py`)

```python
import datetime
import logging

from sqlalchemy.dialects.sqlite import insert

from collector.parse import parse_author, parse_work
from db.models import Authorship, Researcher, SyncState, Work

logger = logging.getLogger(__name__)

INSTITUTION_ID = "I4387152983"  # 大阪公立大学
AUTHOR_SELECT = "id,display_name,orcid,works_count,summary_stats,updated_date"
WORK_SELECT = (
    "id,doi,title,publication_date,type,cited_by_count,fwci,"
    "citation_normalized_percentile,primary_topic,primary_location,"
    "open_access,authorships,updated_date"
)
COMMIT_EVERY = 1000


def window_start(today: datetime.date) -> str:
    return today.replace(year=today.year - 3).isoformat()


def _upsert(session, model, kwargs: dict):
    stmt = insert(model).values(**kwargs)
    pk_cols = [c.name for c in model.__table__.primary_key]
    update_cols = {k: v for k, v in kwargs.items() if k not in pk_cols}
    session.execute(stmt.on_conflict_do_update(
        index_elements=pk_cols, set_=update_cols))


def _record_state(session, source: str, today: datetime.date):
    _upsert(session, SyncState,
            {"source": source, "cursor": None, "last_synced_at": today.isoformat()})
    session.commit()


def sync_authors(session, client, today: datetime.date,
                 institution_id: str = INSTITUTION_ID,
                 since: str | None = None) -> int:
    filter_str = f"last_known_institutions.id:{institution_id}"
    if since:
        filter_str += f",from_updated_date:{since}"
    n = 0
    for rec in client.paginate("authors", filter_str, select=AUTHOR_SELECT):
        _upsert(session, Researcher, parse_author(rec))
        n += 1
        if n % COMMIT_EVERY == 0:
            session.commit()
            logger.info("authors: %d upserted", n)
    _record_state(session, "authors", today)
    logger.info("authors sync done: %d", n)
    return n


def sync_works(session, client, today: datetime.date,
               institution_id: str = INSTITUTION_ID,
               since: str | None = None) -> int:
    filter_str = (f"institutions.id:{institution_id},"
                  f"from_publication_date:{window_start(today)}")
    if since:
        filter_str += f",from_updated_date:{since}"
    n = 0
    for rec in client.paginate("works", filter_str, select=WORK_SELECT):
        work_kw, authorships = parse_work(rec)
        _upsert(session, Work, work_kw)
        for a in authorships:
            _upsert(session, Authorship, a)
        n += 1
        if n % COMMIT_EVERY == 0:
            session.commit()
            logger.info("works: %d upserted", n)
    _record_state(session, "works", today)
    if since is None:  # full同期時のみ全件数を突合
        expected = client.count("works", filter_str)
        if expected != n:
            logger.warning("works count mismatch: api=%d local=%d", expected, n)
    logger.info("works sync done: %d", n)
    return n
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_sync.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add collector/sync.py tests/test_sync.py
git commit -m "feat: authors/works同期（upsert・差分フィルタ・件数突合）"
```

---

### Task 6: 研究者別メトリクス集計

**Files:**
- Create: `collector/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: モデル（Task 2）、`window_start`（Task 5）
- Produces:
  - `collector.metrics.compute_metrics(session, today: datetime.date) -> int` — `researchers` に居る各研究者について、ウィンドウ内（`publication_date >= window_start(today)`）の works を authorships 経由で集計し、`researcher_metrics` を**洗い替え**（全削除→再挿入）。処理した研究者数を返す
  - 集計対象は researchers に行がある著者のみ（外部共著者は無視）
  - fwci_mean / fwci_median は fwci が NULL の論文を除外して計算。対象0件なら None
  - 論文が1本も無い研究者も works_count_3y=0 の行を持つ

- [ ] **Step 1: 失敗するテストを書く** (`tests/test_metrics.py`)

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装** (`collector/metrics.py`)

```python
import datetime
import statistics

from sqlalchemy import delete, select

from collector.sync import window_start
from db.models import Authorship, Researcher, ResearcherMetrics, Work


def compute_metrics(session, today: datetime.date) -> int:
    start = window_start(today)
    session.execute(delete(ResearcherMetrics))

    rows = session.execute(
        select(Authorship.author_id, Authorship.author_position,
               Authorship.is_corresponding, Work.cited_by_count,
               Work.fwci, Work.is_top10pct)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .join(Researcher, Researcher.openalex_id == Authorship.author_id)
        .where(Work.publication_date >= start)
    ).all()

    by_author: dict[str, list] = {}
    for row in rows:
        by_author.setdefault(row.author_id, []).append(row)

    n = 0
    for rid in session.scalars(select(Researcher.openalex_id)):
        items = by_author.get(rid, [])
        fwcis = [r.fwci for r in items if r.fwci is not None]
        session.add(ResearcherMetrics(
            researcher_id=rid,
            works_count_3y=len(items),
            total_citations=sum(r.cited_by_count for r in items),
            fwci_mean=round(statistics.mean(fwcis), 4) if fwcis else None,
            fwci_median=round(statistics.median(fwcis), 4) if fwcis else None,
            top10pct_count=sum(1 for r in items if r.is_top10pct),
            first_author_count=sum(1 for r in items if r.author_position == "first"),
            corresponding_count=sum(1 for r in items if r.is_corresponding),
            computed_at=today.isoformat(),
        ))
        n += 1
    session.commit()
    return n
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add collector/metrics.py tests/test_metrics.py
git commit -m "feat: 研究者別メトリクス集計（3年ウィンドウ・FWCI・top10%）"
```

---

### Task 7: CLI・スモークテスト・README・初回全量同期

**Files:**
- Create: `scripts/sync.py`
- Create: `tests/test_smoke.py`
- Create: `README.md`

**Interfaces:**
- Consumes: Task 2〜6の全部
- Produces:
  - `uv run python scripts/sync.py full` — authors＋works全量同期→metrics再計算
  - `uv run python scripts/sync.py incremental` — `sync_state.last_synced_at` を `since` に使う差分同期→metrics再計算。stateが無ければfullにフォールバック

- [ ] **Step 1: スモークテストを書く** (`tests/test_smoke.py`)

実APIを小さく叩き、フィルタ・selectパラメタが実サーバで有効なことを保証する（`-m "not smoke"` で除外可能）:

```python
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
```

- [ ] **Step 2: スモークテスト実行**

Run: `uv run pytest tests/test_smoke.py -v -m smoke`
Expected: PASS (2 tests、実APIアクセスあり)

- [ ] **Step 3: CLI実装** (`scripts/sync.py`)

```python
import argparse
import datetime
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from collector.metrics import compute_metrics
from collector.openalex import OpenAlexClient
from collector.sync import sync_authors, sync_works
from db.models import SyncState, get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sync")


def main() -> None:
    parser = argparse.ArgumentParser(description="OMU researcher data sync")
    parser.add_argument("mode", choices=["full", "incremental"])
    parser.add_argument("--db", default="db/researchers.db")
    args = parser.parse_args()

    today = datetime.date.today()
    engine = get_engine(args.db)
    client = OpenAlexClient()

    with Session(engine) as session:
        since = None
        if args.mode == "incremental":
            state = session.get(SyncState, "works")
            if state and state.last_synced_at:
                since = state.last_synced_at
            else:
                logger.info("sync_stateが無いためfullにフォールバック")
        n_a = sync_authors(session, client, today=today, since=since)
        n_w = sync_works(session, client, today=today, since=since)
        n_m = compute_metrics(session, today)
        logger.info("done: authors=%d works=%d metrics=%d", n_a, n_w, n_m)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 全テスト実行**

Run: `uv run pytest -v -m "not smoke"`
Expected: 全てPASS

- [ ] **Step 5: 初回全量同期の実行**

Run: `uv run python scripts/sync.py full`
Expected: 数分程度（200件/ページ×約55リクエスト、10 rps上限内）。完了ログに `done: authors=~3900 works=~6200 metrics=~3900` 相当の数字。`works count mismatch` 警告が出ないこと

確認クエリ:

```bash
sqlite3 db/researchers.db "
  SELECT COUNT(*) FROM researchers;
  SELECT COUNT(*) FROM works;
  SELECT r.display_name, m.works_count_3y, m.total_citations, m.fwci_mean
  FROM researcher_metrics m JOIN researchers r ON r.openalex_id=m.researcher_id
  ORDER BY m.total_citations DESC LIMIT 10;"
```

Expected: researchers≈3,900 / works≈6,200 / 上位10人が表示され、値が非ゼロ

- [ ] **Step 6: README を書く** (`README.md`)

```markdown
# OMU Researchers Data Pipeline

大阪公立大学の研究者一覧と直近3年の論文・被引用データをOpenAlexから収集するデータ基盤。
設計書: `docs/superpowers/specs/2026-07-02-omu-researchers-data-design.md`

## セットアップ

    uv sync

## 使い方

    uv run python scripts/sync.py full         # 全量同期＋メトリクス再計算
    uv run python scripts/sync.py incremental  # 前回以降の差分のみ

## cron（推奨）

    # 週1回 差分同期（月曜 06:00）
    0 6 * * 1 cd /srv/apps/researchers && uv run python scripts/sync.py incremental >> logs/sync.log 2>&1
    # 月1回 全量洗い替え（毎月1日 05:00、被引用数の更新を反映）
    0 5 1 * * cd /srv/apps/researchers && uv run python scripts/sync.py full >> logs/sync.log 2>&1

## テスト

    uv run pytest -m "not smoke"   # オフライン
    uv run pytest -m smoke         # 実APIスモーク

## 注意

- 集計値は「OpenAlex収録分に基づく」もの。全業績の断定表現は使わない
- 研究者間比較には生被引用数でなく fwci_mean / top10pct_count を第一に使う
```

- [ ] **Step 7: Commit**

```bash
mkdir -p logs && touch logs/.gitkeep
git add scripts/sync.py tests/test_smoke.py README.md logs/.gitkeep
git commit -m "feat: sync CLI・実APIスモークテスト・README"
```

---

## 完了条件

- `uv run pytest -m "not smoke"` が全件PASS
- `db/researchers.db` に researchers≈3,900 / works≈6,200 / researcher_metrics が入っている
- `scripts/sync.py incremental` が2回目以降エラーなく短時間で完了する
