# works駆動の研究者補完 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OpenAlexの機関名寄せ誤解決でリストから漏れている研究者（例: 三木幸雄教授）を、取得済みworksのauthorship所属情報から機械的に補完する。

**Architecture:** works APIレスポンスに含まれる著者ごとの所属機関IDを `authorships.institution_ids` に保存し、works同期の機関フィルタを前身3機関込みに拡張。同期後に「対象機関名義のauthorshipを持つのにresearchersに居ない著者」をバッチ取得して `source='works'` で補完する。既存のdedup・名簿名寄せ・metricsはそのまま乗る。

**Tech Stack:** Python 3 / SQLAlchemy / SQLite / httpx / pytest（オフラインはFakeClient、実APIは `-m smoke`）

**Spec:** `docs/superpowers/specs/2026-07-16-works-backfill-design.md`

## Global Constraints

- 対象機関ID: 大阪公立大学 I4387152983、旧大阪市立大学 I317356780、旧大阪府立大学 I15807432、市大病院 I4210166029（高専は含めない）
- migrationは書かない。スキーマ変更は `rm db/researchers.db*` → 全量再同期で反映（README記載の標準手順）
- `researchers.source` は `'last_known'`（既定）| `'works'` の2値
- `authorships.institution_ids` はパイプ区切りTEXT（例 `"I4387152983|I100"`）、機関なしは NULL
- オフラインテストは `uv run pytest -m "not smoke"` で全件パスすること
- コミットメッセージは既存流儀（`feat:` / `test:` / `docs:` + 日本語）

---

### Task 1: スキーマ2列追加と parse_work の institution_ids 抽出

**Files:**
- Modify: `db/models.py` （Researcher / Authorship に列追加）
- Modify: `collector/parse.py:67-77` （authorshipsにinstitution_idsを含める）
- Test: `tests/test_parse.py`

**Interfaces:**
- Produces: `Researcher.source: str`（default `"last_known"`）、`Authorship.institution_ids: str | None`、`parse_work` が返すauthorship dictに `"institution_ids"` キー追加

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_parse.py` の `test_parse_work` 内の既存アサーションを更新し、新規テスト2本を追加：

```python
# test_parse_work 内の auths[0] の等価アサーションを差し替え
    assert auths[0] == {"work_id": "W4385564466", "author_id": "A5023888391",
                        "author_position": "first", "is_corresponding": True,
                        "institution_ids": "I4387152983"}
```

```python
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
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_parse.py -v`
Expected: `test_parse_work` / `test_parse_work_authorship_institution_ids` / `test_parse_work_authorship_without_institutions` がFAIL（KeyError: 'institution_ids' 等）

- [ ] **Step 3: 実装**

`db/models.py` — Researcherの `canonical_id` の下に追加：

```python
    # 'last_known': last_known_institutionsフィルタ由来 / 'works': works補完由来
    source: Mapped[str] = mapped_column(String, default="last_known")
```

Authorshipの `is_corresponding` の下に追加：

```python
    # その著者のその論文での所属機関ID（パイプ区切り、なければNULL）
    institution_ids: Mapped[str | None] = mapped_column(String, nullable=True)
```

`collector/parse.py` — `parse_work` 末尾のauthorships内包表記をループに差し替え：

```python
    authorships = []
    for a in auth_list:
        author_id = strip_id((a.get("author") or {}).get("id"))
        if not author_id:
            continue
        inst_ids = [strip_id(inst.get("id"))
                    for inst in (a.get("institutions") or []) if inst.get("id")]
        authorships.append({
            "work_id": work_id,
            "author_id": author_id,
            "author_position": a.get("author_position"),
            "is_corresponding": bool(a.get("is_corresponding", False)),
            "institution_ids": "|".join(inst_ids) if inst_ids else None,
        })
    return work_kw, authorships
```

- [ ] **Step 4: テストがパスすることを確認**

Run: `uv run pytest tests/test_parse.py tests/test_models.py -v`
Expected: 全てPASS

- [ ] **Step 5: コミット**

```bash
git add db/models.py collector/parse.py tests/test_parse.py
git commit -m "feat: authorshipに所属機関ID、researcherにsource列を追加"
```

### Task 2: works同期の機関フィルタ拡張と authors削除パスのsource保護

**Files:**
- Modify: `collector/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: Task 1の `Researcher.source`、`parse_work` の institution_ids
- Produces: `TARGET_INSTITUTION_IDS: tuple[str, ...]`（4機関）、`sync_works(session, client, today, institution_ids=TARGET_INSTITUTION_IDS)`、`sync_authors` はupsert時に `source="last_known"` を明示し、削除パスは `source == "last_known"` の行のみ対象

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_sync.py` に追加・修正：

```python
# test_sync_works_upserts_works_and_authorships の末尾assertを差し替え
    assert ("institutions.id:I4387152983|I317356780|I15807432|I4210166029"
            in client.calls[0][1])
    assert "from_publication_date:2023-07-02" in client.calls[0][1]
```

```python
def test_sync_works_stores_institution_ids():
    engine = get_engine(":memory:")
    client = FakeClient([WORK], count_value=1)
    with Session(engine) as s:
        sync_works(s, client, today=TODAY)
        a = s.get(Authorship, ("W4385564466", "A5023888391"))
        assert a.institution_ids == "I4387152983"


def test_sync_authors_sets_source_and_keeps_works_rows():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A_WORKS", display_name="Backfilled",
                         source="works", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeClient([AUTHOR], count_value=1)
        sync_authors(s, client, today=TODAY)
        # works由来の行は削除パスの対象外
        assert s.get(Researcher, "A_WORKS") is not None
        assert s.get(Researcher, "A5023888391").source == "last_known"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 上記3テストがFAIL（フィルタ文字列不一致、institution_ids=None、A_WORKSが削除される）

- [ ] **Step 3: 実装**

`collector/sync.py` — 定数を拡張：

```python
INSTITUTION_ID = "I4387152983"  # 大阪公立大学
# OpenAlexの機関名寄せは不安定で、OMUの所属文字列が前身機関等に誤解決されるため
# works取得と著者補完は前身機関込みで行う（設計書 2026-07-16-works-backfill-design.md）
PREDECESSOR_IDS = ("I317356780", "I15807432", "I4210166029")  # 旧市大・旧府大・市大病院
TARGET_INSTITUTION_IDS = (INSTITUTION_ID, *PREDECESSOR_IDS)
```

`sync_authors` — upsertとdelete文を変更：

```python
        kw = parse_author(rec)
        seen_ids.add(kw["openalex_id"])
        _upsert(session, Researcher, {**kw, "source": "last_known"})
```

```python
        n_del = session.execute(
            delete(Researcher).where(Researcher.openalex_id.not_in(seen_ids),
                                     Researcher.source == "last_known")
        ).rowcount
```

`sync_works` — シグネチャとフィルタを変更：

```python
def sync_works(session, client, today: datetime.date,
               institution_ids: tuple[str, ...] = TARGET_INSTITUTION_IDS) -> int:
    start = window_start(today)
    filter_str = (f"institutions.id:{'|'.join(institution_ids)},"
                  f"from_publication_date:{start}")
```

- [ ] **Step 4: テストがパスすることを確認**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 全てPASS

- [ ] **Step 5: コミット**

```bash
git add collector/sync.py tests/test_sync.py
git commit -m "feat: works同期を前身機関込みに拡張しauthors削除パスをsourceで保護"
```

### Task 3: backfill_authors モジュール

**Files:**
- Create: `collector/backfill.py`
- Test: `tests/test_backfill.py`（新規）

**Interfaces:**
- Consumes: `collector.sync` の `AUTHOR_SELECT` / `TARGET_INSTITUTION_IDS` / `_upsert` / `_record_state`、`collector.parse.parse_author`
- Produces: `backfill_authors(session, client, today: datetime.date) -> int`（補完・更新した著者数を返す）。clientは `paginate(endpoint, filter_str, select=..., per_page=...)` を持つ既存 `OpenAlexClient` 互換

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_backfill.py` を新規作成：

```python
import datetime

from sqlalchemy.orm import Session

from collector.backfill import backfill_authors
from db.models import Authorship, Researcher, SyncState, Work, get_engine

TODAY = datetime.date(2026, 7, 16)


class FakeAuthorsClient:
    """ids.openalex:A|B|... フィルタに応じて登録済みレコードだけ返す"""

    def __init__(self, records):
        self.records = {r["id"].rsplit("/", 1)[-1]: r for r in records}
        self.calls = []

    def paginate(self, endpoint, filter_str, select=None, per_page=50):
        self.calls.append((endpoint, filter_str))
        assert endpoint == "authors"
        ids = filter_str.removeprefix("ids.openalex:").split("|")
        assert len(ids) <= per_page
        for i in ids:
            if i in self.records:
                yield self.records[i]


def _author(aid, name):
    return {"id": f"https://openalex.org/{aid}", "display_name": name,
            "works_count": 10, "summary_stats": {"h_index": 5},
            "updated_date": "2026-07-01T00:00:00"}


def _seed_work(s, work_id):
    s.add(Work(openalex_id=work_id, title="t", publication_date="2025-01-01",
               raw_json="{}", updated_at=""))


def _seed(s, work_id, author_id, inst_ids):
    s.add(Authorship(work_id=work_id, author_id=author_id,
                     author_position="first", is_corresponding=False,
                     institution_ids=inst_ids))


def test_backfill_adds_qualifying_missing_author():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1")
        _seed(s, "W1", "A_NEW", "I4387152983|I100")   # OMU名義 → 対象
        _seed(s, "W1", "A_EXT", "I100")               # 学外のみ → 対象外
        _seed(s, "W1", "A_NULL", None)                # 所属なし → 対象外
        s.commit()
        client = FakeAuthorsClient([_author("A_NEW", "New Researcher"),
                                    _author("A_EXT", "External")])
        n = backfill_authors(s, client, today=TODAY)
        assert n == 1
        r = s.get(Researcher, "A_NEW")
        assert r is not None and r.source == "works"
        assert s.get(Researcher, "A_EXT") is None
        assert s.get(Researcher, "A_NULL") is None
        assert s.get(SyncState, "backfill").last_synced_at == "2026-07-16"


def test_backfill_predecessor_institution_qualifies():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1")
        _seed(s, "W1", "A_OCU", "I317356780")  # 旧市大名義
        s.commit()
        client = FakeAuthorsClient([_author("A_OCU", "Ocu Researcher")])
        assert backfill_authors(s, client, today=TODAY) == 1
        assert s.get(Researcher, "A_OCU").source == "works"


def test_backfill_does_not_refetch_last_known_researchers():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1")
        _seed(s, "W1", "A_LK", "I4387152983")
        s.add(Researcher(openalex_id="A_LK", display_name="Existing",
                         source="last_known", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeAuthorsClient([_author("A_LK", "Existing")])
        assert backfill_authors(s, client, today=TODAY) == 0
        assert client.calls == []
        assert s.get(Researcher, "A_LK").source == "last_known"


def test_backfill_removes_stale_works_rows_only():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A_STALE: works由来だが対象authorshipなし → 削除
        # A_LK: last_known由来でauthorshipなし → 保持
        s.add(Researcher(openalex_id="A_STALE", display_name="Stale",
                         source="works", raw_json="{}", updated_at=""))
        s.add(Researcher(openalex_id="A_LK", display_name="Keep",
                         source="last_known", raw_json="{}", updated_at=""))
        s.commit()
        client = FakeAuthorsClient([])
        backfill_authors(s, client, today=TODAY)
        assert s.get(Researcher, "A_STALE") is None
        assert s.get(Researcher, "A_LK") is not None


def test_backfill_skips_unfetchable_ids_with_warning(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _seed_work(s, "W1")
        _seed(s, "W1", "A_OK", "I4387152983")
        _seed(s, "W1", "A_GONE", "I4387152983")  # APIが返さない（マージ消滅）
        s.commit()
        client = FakeAuthorsClient([_author("A_OK", "Ok Researcher")])
        with caplog.at_level("WARNING"):
            n = backfill_authors(s, client, today=TODAY)
        assert n == 1
        assert s.get(Researcher, "A_OK") is not None
        assert s.get(Researcher, "A_GONE") is None
    assert any("スキップ" in r.message for r in caplog.records)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `uv run pytest tests/test_backfill.py -v`
Expected: FAIL（ModuleNotFoundError: collector.backfill）

- [ ] **Step 3: 実装**

`collector/backfill.py` を新規作成：

```python
import datetime
import logging

from sqlalchemy import delete, select

from collector.parse import parse_author
from collector.sync import (AUTHOR_SELECT, TARGET_INSTITUTION_IDS,
                            _record_state, _upsert)
from db.models import Authorship, Researcher

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # ids.openalexフィルタの1リクエストあたり著者数


def qualifying_author_ids(
        session,
        institution_ids: tuple[str, ...] = TARGET_INSTITUTION_IDS) -> set[str]:
    """対象機関名義のauthorshipを1件以上持つ著者ID"""
    target = set(institution_ids)
    qualifying: set[str] = set()
    for author_id, inst_ids in session.execute(
            select(Authorship.author_id, Authorship.institution_ids)
            .where(Authorship.institution_ids.is_not(None))):
        if target & set(inst_ids.split("|")):
            qualifying.add(author_id)
    return qualifying


def backfill_authors(session, client, today: datetime.date) -> int:
    qualifying = qualifying_author_ids(session)
    last_known = set(session.scalars(
        select(Researcher.openalex_id)
        .where(Researcher.source == "last_known")))
    to_fetch = sorted(qualifying - last_known)
    n = 0
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[i:i + BATCH_SIZE]
        filter_str = "ids.openalex:" + "|".join(batch)
        got: set[str] = set()
        for rec in client.paginate("authors", filter_str,
                                   select=AUTHOR_SELECT, per_page=BATCH_SIZE):
            kw = parse_author(rec)
            got.add(kw["openalex_id"])
            _upsert(session, Researcher, {**kw, "source": "works"})
            n += 1
        skipped = set(batch) - got
        if skipped:
            logger.warning(
                "backfill: %d件の著者が取得できずスキップ（マージ消滅等）: %s",
                len(skipped), sorted(skipped)[:5])
        session.commit()

    works_sourced = set(session.scalars(
        select(Researcher.openalex_id).where(Researcher.source == "works")))
    stale = works_sourced - qualifying
    if stale:
        session.execute(
            delete(Researcher).where(Researcher.openalex_id.in_(stale)))
        logger.info("backfill: %d件のworks由来研究者を削除（対象authorship消滅）",
                    len(stale))
    _record_state(session, "backfill", today)
    logger.info("backfill done: %d人補完/更新（対象著者%d人）", n, len(qualifying))
    return n
```

- [ ] **Step 4: テストがパスすることを確認**

Run: `uv run pytest tests/test_backfill.py -v`
Expected: 全てPASS

- [ ] **Step 5: コミット**

```bash
git add collector/backfill.py tests/test_backfill.py
git commit -m "feat: OMU名義authorshipを持つ著者をresearchersに補完するbackfill"
```

### Task 4: パイプライン組み込みとREADME更新

**Files:**
- Modify: `scripts/sync.py:38` 付近（works直後にbackfill）
- Modify: `README.md`（注意セクションに1行）

**Interfaces:**
- Consumes: Task 3の `backfill_authors(session, client, today)`

- [ ] **Step 1: scripts/sync.py にbackfillを組み込む**

import追加：

```python
from collector.backfill import backfill_authors
```

`n_w = sync_works(...)` の直後に挿入（dedupより前）：

```python
        try:
            n_b = backfill_authors(session, client, today=today)
            logger.info("backfill: %d authors", n_b)
        except Exception:
            session.rollback()
            logger.exception("backfillに失敗（他ステージは継続）")
```

- [ ] **Step 2: README の注意セクションに追記**

「集計値は…」の行の下に追加：

```markdown
- works取得と研究者補完は前身機関名義（旧市大・旧府大・市大病院）も対象。OpenAlexの機関名寄せ誤解決で漏れる研究者への対策（設計書 2026-07-16-works-backfill-design.md）
```

- [ ] **Step 3: オフラインテスト全件を実行**

Run: `uv run pytest -m "not smoke"`
Expected: 全てPASS

- [ ] **Step 4: コミット**

```bash
git add scripts/sync.py README.md
git commit -m "feat: 同期パイプラインにbackfillを組み込み"
```

### Task 5: DB再構築と実データ検証

**Files:**
- なし（運用手順）

- [ ] **Step 1: 現DBを退避して再構築**

```bash
mv db/researchers.db db/researchers.db.bak-20260716
rm -f db/researchers.db-wal db/researchers.db-shm
```

- [ ] **Step 2: 全量同期を実行（数分〜20分、roster/profileクロール含む）**

Run: `uv run python scripts/sync.py 2>&1 | tee logs/sync-backfill-initial.log`
Expected: authors / works / backfill / dedup / kaken / roster / metrics が全て完走し、backfillが数百〜数千人を補完

- [ ] **Step 3: 検証クエリ**

```bash
uv run python -c "
import sqlite3
con = sqlite3.connect('db/researchers.db')
print('researchers:', con.execute('select count(*) from researchers').fetchone()[0])
print('  works由来:', con.execute(\"select count(*) from researchers where source='works'\").fetchone()[0])
print('works:', con.execute('select count(*) from works').fetchone()[0])
print('roster未マッチ:', con.execute('select count(*) from roster where matched_researcher_id is null').fetchone()[0])
print('三木幸雄:', con.execute('''
  select r.openalex_id, r.source, m.works_count_3y, m.total_citations
  from researchers r join researcher_metrics m on m.researcher_id = r.openalex_id
  where r.name_ja = '三木 幸雄' ''').fetchall())
"
```

Expected: 三木幸雄の works_count_3y が70前後、roster未マッチが808から有意に減少、worksが約9,700件

- [ ] **Step 4: Webサービス再起動（DBファイルを作り直したため）**

Run: `sudo -n systemctl restart omu-researchers-web || echo "要手動: sudo systemctl restart omu-researchers-web"`

- [ ] **Step 5: 検証結果が良好なら退避DBを削除**

```bash
rm db/researchers.db.bak-20260716
```
