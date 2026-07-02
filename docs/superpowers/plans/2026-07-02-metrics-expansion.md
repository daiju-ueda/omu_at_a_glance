# Tier 1 評価指標拡充＋ランキング既定表示 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保存済みraw_jsonから計算できる11の追加評価指標（著者数補正・国際/産学共著率・top1%・i10等）を収集し、ランキング既定を「1本以上・母数内訳表示」に変える。

**Architecture:** parse層でwork/authorレコードから新フィールドを抽出（works: n_authors/is_intl_collab/is_corp_collab、researchers: i10_index/two_yr_mean_citedness）、metrics層で11指標を集計。マイグレーションは書かず**DB削除→full再同期**で再構築。Webはランキング既定変更＋列追加＋詳細カード拡充。

**Tech Stack:** 既存スタックのみ（SQLAlchemy / FastAPI / Jinja2）。追加依存なし。

**設計書:** `docs/superpowers/specs/2026-07-02-metrics-expansion-design.md`

## Global Constraints

- 追加API取得ゼロ（全て保存済みraw_json・既存列から計算）
- fractional系: n_authors=0 の論文は除数1として扱う。fractional_works/fractional_citations/avg_authors/各rateは小数4桁丸め
- rate系（intl_collab_rate/corp_collab_rate/oa_rate）と avg_authors は論文0本なら None
- top_subfield: ウィンドウ内worksのsubfield最頻値。同数タイは辞書順で先のもの。subfield全てNULLなら None
- unique_coauthors: ウィンドウ内worksの共著者（外部著者含む）のユニーク数から本人を除く
- is_intl_collab: いずれかのauthorshipの `countries` にJP以外が含まれる。is_corp_collab: いずれかの `institutions[].type == "company"`
- Webランキング: 既定 `min_works=1`（不正値・範囲外のフォールバックも1）、内訳表示「全{total_all}人中 {total}人を表示」、ソートキーに `fractional_citations` を追加（ホワイトリスト5キー）
- 率の表示は `pct` フィルタ（None→「–」、それ以外 `{value*100:.0f}%`）
- DBスキーマ変更の運用: migrationコードは書かない。`db/researchers.db*` を削除して full sync で再構築（READMEに明記）

---

### Task 1: モデル＋パーサ拡張

**Files:**
- Modify: `db/models.py`（Work/Researcher/ResearcherMetricsに列追加）
- Modify: `collector/parse.py`（新フィールド抽出）
- Test: `tests/test_parse.py`（fixture拡張＋新アサーション）

**Interfaces:**
- Produces:
  - `Work` 追加列: `n_authors: int (default 0)`, `is_intl_collab: bool (default False)`, `is_corp_collab: bool (default False)`
  - `Researcher` 追加列: `i10_index: int (default 0)`, `two_yr_mean_citedness: float|None`
  - `ResearcherMetrics` 追加列: `top1pct_count: int (default 0)`, `fractional_works: float|None`, `fractional_citations: float|None`, `avg_authors: float|None`, `intl_collab_rate: float|None`, `corp_collab_rate: float|None`, `oa_rate: float|None`, `preprint_count: int (default 0)`, `dataset_software_count: int (default 0)`, `unique_coauthors: int (default 0)`, `top_subfield: str|None`
  - `parse_author` 返り値に `i10_index`, `two_yr_mean_citedness` を追加
  - `parse_work` 返り値のwork側に `n_authors`, `is_intl_collab`, `is_corp_collab` を追加（authorship側は不変）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_parse.py` を以下の通り変更）

AUTHOR fixture の `summary_stats` 行を差し替え:

```python
    "summary_stats": {"h_index": 25, "i10_index": 100,
                      "2yr_mean_citedness": 2.5},
```

WORK fixture の `authorships` を差し替え（countries/institutionsを追加。実APIの形状）:

```python
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
```

`test_parse_author` に追記:

```python
    assert kw["i10_index"] == 100
    assert kw["two_yr_mean_citedness"] == 2.5
```

`test_parse_author_missing_fields` に追記:

```python
    assert kw["i10_index"] == 0
    assert kw["two_yr_mean_citedness"] is None
```

`test_parse_work` に追記:

```python
    assert work_kw["n_authors"] == 2
    assert work_kw["is_intl_collab"] is True   # USの共著者あり
    assert work_kw["is_corp_collab"] is False  # companyなし
```

`test_parse_work_missing_fields` に追記:

```python
    assert work_kw["n_authors"] == 0
    assert work_kw["is_intl_collab"] is False
    assert work_kw["is_corp_collab"] is False
```

新規テスト2本を追加:

```python
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
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_parse.py -v`
Expected: FAIL（新キー不在の KeyError / AssertionError）

- [ ] **Step 3: モデルに列を追加**（`db/models.py`）

`Researcher` クラスの `works_count` 行の直後に追加:

```python
    i10_index: Mapped[int] = mapped_column(Integer, default=0)
    two_yr_mean_citedness: Mapped[float | None] = mapped_column(
        Float, nullable=True)
```

`Work` クラスの `is_oa` 行の直後に追加:

```python
    n_authors: Mapped[int] = mapped_column(Integer, default=0)
    is_intl_collab: Mapped[bool] = mapped_column(Boolean, default=False)
    is_corp_collab: Mapped[bool] = mapped_column(Boolean, default=False)
```

`ResearcherMetrics` クラスの `corresponding_count` 行の直後に追加:

```python
    top1pct_count: Mapped[int] = mapped_column(Integer, default=0)
    fractional_works: Mapped[float | None] = mapped_column(Float, nullable=True)
    fractional_citations: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    avg_authors: Mapped[float | None] = mapped_column(Float, nullable=True)
    intl_collab_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    corp_collab_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    oa_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    preprint_count: Mapped[int] = mapped_column(Integer, default=0)
    dataset_software_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_coauthors: Mapped[int] = mapped_column(Integer, default=0)
    top_subfield: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: パーサを拡張**（`collector/parse.py`）

`parse_author` の返り値dictの `"works_count"` 行の直後に追加:

```python
        "i10_index": (rec.get("summary_stats") or {}).get("i10_index") or 0,
        "two_yr_mean_citedness": (rec.get("summary_stats") or {}).get(
            "2yr_mean_citedness"),
```

`parse_work` の冒頭（`cnp = ...` の前）に追加:

```python
    auth_list = rec.get("authorships") or []
    countries = {c for a in auth_list for c in (a.get("countries") or [])}
    has_corp = any(
        inst.get("type") == "company"
        for a in auth_list for inst in (a.get("institutions") or []))
```

`work_kw` の `"is_oa"` 行の直後に追加:

```python
        "n_authors": len(auth_list),
        "is_intl_collab": bool(countries - {"JP"}),
        "is_corp_collab": has_corp,
```

既存の authorships 内包表記は `rec.get("authorships") or []` を `auth_list` に置き換える（挙動不変）。

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_parse.py -v`
Expected: PASS (7 tests)。続けて `uv run pytest -m "not smoke"` 全件PASS

- [ ] **Step 6: Commit**

```bash
git add db/models.py collector/parse.py tests/test_parse.py
git commit -m "feat: works/researchersに国際・産学・著者数・i10フィールドを追加"
```

---

### Task 2: メトリクス集計の拡張

**Files:**
- Modify: `collector/metrics.py`（compute_metricsを差し替え）
- Test: `tests/test_metrics.py`（全面書き換え）

**Interfaces:**
- Consumes: Task 1のWork/ResearcherMetrics新列
- Produces: `compute_metrics(session, today)` が11の新指標も計算する（シグネチャ不変）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_metrics.py` を以下の内容に全面置き換え）

```python
import datetime

from sqlalchemy.orm import Session

from collector.metrics import compute_metrics
from db.models import Authorship, Researcher, ResearcherMetrics, Work, get_engine

TODAY = datetime.date(2026, 7, 2)


def _researcher(id_):
    return Researcher(openalex_id=id_, display_name=id_, h_index=1,
                      works_count=1, raw_json="{}", updated_at="")


def _work(id_, date, cites, fwci, top10, *, top1=False, n_authors=1,
          intl=False, corp=False, oa=False, type_="article", subfield=None):
    return Work(openalex_id=id_, title=id_, publication_date=date,
                cited_by_count=cites, fwci=fwci, is_top1pct=top1,
                is_top10pct=top10, is_oa=oa, type=type_, subfield=subfield,
                n_authors=n_authors, is_intl_collab=intl, is_corp_collab=corp,
                raw_json="{}", updated_at="")


def _auth(work_id, author_id, position="middle", corresponding=False):
    return Authorship(work_id=work_id, author_id=author_id,
                      author_position=position, is_corresponding=corresponding)


def test_compute_metrics():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([_researcher("A1"), _researcher("A2"), _researcher("A5")])
        s.add_all([
            _work("W1", "2024-01-01", 10, 2.0, True, top1=True, n_authors=2,
                  intl=True, oa=True, subfield="ML"),
            _work("W2", "2025-01-01", 4, 1.0, False, n_authors=4, corp=True,
                  type_="preprint", subfield="ML"),
            _work("W3", "2020-01-01", 100, 9.0, True),          # ウィンドウ外
            _work("W4", "2024-06-01", 6, None, False, n_authors=0,
                  type_="dataset"),                              # 著者数0→除数1
            _work("W5", "2024-02-01", 1, None, False, subfield="ML"),
            _work("W6", "2024-03-01", 1, None, False, subfield="AI"),
        ])
        s.add_all([
            _auth("W1", "A1", position="first", corresponding=True),
            _auth("W2", "A1", position="last"),
            _auth("W3", "A1", position="first", corresponding=True),
            _auth("W4", "A1"),
            _auth("W1", "A9"),           # researchersに居ない外部共著者
            _auth("W5", "A5"),
            _auth("W6", "A5"),
        ])
        s.commit()

        n = compute_metrics(s, TODAY)
        assert n == 3

        m1 = s.get(ResearcherMetrics, "A1")
        # 既存指標（W1, W2, W4がウィンドウ内）
        assert m1.works_count_3y == 3
        assert m1.total_citations == 20
        assert m1.fwci_mean == 1.5
        assert m1.fwci_median == 1.5
        assert m1.top10pct_count == 1
        assert m1.first_author_count == 1
        assert m1.corresponding_count == 1
        # 新指標
        assert m1.top1pct_count == 1
        assert m1.fractional_works == 1.75          # 1/2 + 1/4 + 1/1
        assert m1.fractional_citations == 12.0      # 10/2 + 4/4 + 6/1
        assert m1.avg_authors == 2.3333             # (2+4+1)/3
        assert m1.intl_collab_rate == 0.3333        # W1のみ
        assert m1.corp_collab_rate == 0.3333        # W2のみ
        assert m1.oa_rate == 0.3333                 # W1のみ
        assert m1.preprint_count == 1               # W2
        assert m1.dataset_software_count == 1       # W4
        assert m1.unique_coauthors == 1             # A9のみ（本人除外）
        assert m1.top_subfield == "ML"

        m2 = s.get(ResearcherMetrics, "A2")         # 論文ゼロ
        assert m2.works_count_3y == 0
        assert m2.fwci_mean is None
        assert m2.fractional_works == 0
        assert m2.avg_authors is None
        assert m2.intl_collab_rate is None
        assert m2.oa_rate is None
        assert m2.top_subfield is None
        assert m2.unique_coauthors == 0

        m5 = s.get(ResearcherMetrics, "A5")         # subfield同数タイ
        assert m5.top_subfield == "AI"              # 辞書順で先

        assert s.get(ResearcherMetrics, "A9") is None

        compute_metrics(s, TODAY)                   # 洗い替え・冪等
        assert s.query(ResearcherMetrics).count() == 3
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL（新属性のAssertionError等）

- [ ] **Step 3: 実装**（`collector/metrics.py` を以下の内容に全面置き換え）

```python
import collections
import datetime
import statistics

from sqlalchemy import delete, select

from collector.sync import window_start
from db.models import Authorship, Researcher, ResearcherMetrics, Work


def _rate(count, total):
    return round(count / total, 4) if total else None


def compute_metrics(session, today: datetime.date) -> int:
    start = window_start(today)
    session.execute(delete(ResearcherMetrics))

    rows = session.execute(
        select(Authorship.author_id, Authorship.author_position,
               Authorship.is_corresponding, Work.openalex_id,
               Work.cited_by_count, Work.fwci, Work.is_top10pct,
               Work.is_top1pct, Work.n_authors, Work.is_intl_collab,
               Work.is_corp_collab, Work.is_oa, Work.type, Work.subfield)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .join(Researcher, Researcher.openalex_id == Authorship.author_id)
        .where(Work.publication_date >= start)
    ).all()

    # ウィンドウ内の各workの全著者（外部共著者含む）
    by_work: dict[str, set[str]] = {}
    for work_id, author_id in session.execute(
        select(Authorship.work_id, Authorship.author_id)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .where(Work.publication_date >= start)
    ):
        by_work.setdefault(work_id, set()).add(author_id)

    by_author: dict[str, list] = {}
    for row in rows:
        by_author.setdefault(row.author_id, []).append(row)

    n = 0
    for rid in session.scalars(select(Researcher.openalex_id)):
        items = by_author.get(rid, [])
        n_works = len(items)
        fwcis = [r.fwci for r in items if r.fwci is not None]
        divisors = [max(r.n_authors, 1) for r in items]
        partners: set[str] = set()
        for r in items:
            partners |= by_work.get(r.openalex_id, set())
        partners.discard(rid)
        top_subfield = None
        subfields = [r.subfield for r in items if r.subfield]
        if subfields:
            counts = collections.Counter(subfields)
            best = max(counts.values())
            top_subfield = min(k for k, v in counts.items() if v == best)
        session.add(ResearcherMetrics(
            researcher_id=rid,
            works_count_3y=n_works,
            total_citations=sum(r.cited_by_count for r in items),
            fwci_mean=round(statistics.mean(fwcis), 4) if fwcis else None,
            fwci_median=round(statistics.median(fwcis), 4) if fwcis else None,
            top10pct_count=sum(1 for r in items if r.is_top10pct),
            top1pct_count=sum(1 for r in items if r.is_top1pct),
            first_author_count=sum(
                1 for r in items if r.author_position == "first"),
            corresponding_count=sum(1 for r in items if r.is_corresponding),
            fractional_works=round(sum(1 / d for d in divisors), 4),
            fractional_citations=round(
                sum(r.cited_by_count / d for r, d in zip(items, divisors)), 4),
            avg_authors=round(statistics.mean(divisors), 4) if items else None,
            intl_collab_rate=_rate(
                sum(1 for r in items if r.is_intl_collab), n_works),
            corp_collab_rate=_rate(
                sum(1 for r in items if r.is_corp_collab), n_works),
            oa_rate=_rate(sum(1 for r in items if r.is_oa), n_works),
            preprint_count=sum(1 for r in items if r.type == "preprint"),
            dataset_software_count=sum(
                1 for r in items if r.type in ("dataset", "software")),
            unique_coauthors=len(partners),
            top_subfield=top_subfield,
            computed_at=today.isoformat(),
        ))
        n += 1
    session.commit()
    return n
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_metrics.py -v` → PASS。続けて `uv run pytest -m "not smoke"` 全件PASS

- [ ] **Step 5: Commit**

```bash
git add collector/metrics.py tests/test_metrics.py
git commit -m "feat: 著者数補正・国際/産学率・top1%等の11指標を集計に追加"
```

---

### Task 3: DB再構築＋README追記

**Files:**
- Modify: `README.md`（注意セクションに運用1行追加）
- 実行のみ: DB削除→full sync→検証

**Interfaces:**
- Consumes: Task 1-2の全て
- Produces: 新スキーマ＋新指標入りの `db/researchers.db`

- [ ] **Step 1: README追記**

`## 注意` セクション末尾にbullet追加:

```markdown
- スキーマ変更時はmigrationせず `rm db/researchers.db*` →フル同期で再構築する（全データはOpenAlexから数分で再取得できる）
```

- [ ] **Step 2: DB再構築（実API・数分）**

```bash
rm -f db/researchers.db db/researchers.db-wal db/researchers.db-shm
uv run python scripts/sync.py
```

Expected: `done: authors=~3876 works=~6200 metrics=~3876`、mismatch警告なし

- [ ] **Step 3: 新指標のスポットチェック**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
q = lambda sql: c.execute(sql).fetchone()
print('works n_authors>0:', q('SELECT COUNT(*) FROM works WHERE n_authors > 0'))
print('intl works:', q('SELECT COUNT(*) FROM works WHERE is_intl_collab = 1'))
print('corp works:', q('SELECT COUNT(*) FROM works WHERE is_corp_collab = 1'))
print('researchers i10>0:', q('SELECT COUNT(*) FROM researchers WHERE i10_index > 0'))
print('metrics fractional not null:', q('SELECT COUNT(*) FROM researcher_metrics WHERE fractional_citations IS NOT NULL'))
print('metrics top_subfield not null:', q('SELECT COUNT(*) FROM researcher_metrics WHERE top_subfield IS NOT NULL'))
print('sample:', c.execute('SELECT researcher_id, fractional_citations, intl_collab_rate, top_subfield FROM researcher_metrics WHERE works_count_3y >= 5 LIMIT 3').fetchall())
"
```

Expected: works n_authors>0 がほぼ全件（≈6,200）、intl/corpが妥当な割合（intlは千件規模、corpは百件規模の見込み）、i10>0が数千、fractional非NULLが3,876、top_subfield非NULLが3,300前後、sampleに実数値

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: スキーマ変更時のDB再構築運用をREADMEに明記"
```

---

### Task 4: Web拡張（既定1本＋内訳＋新列＋詳細カード）

**Files:**
- Modify: `web/queries.py`（ranking拡張・ソートキー追加）
- Modify: `web/app.py`（既定値・pctフィルタ・context）
- Modify: `web/templates/ranking.html`, `web/templates/researcher.html`
- Modify: `tests/conftest.py`（seed拡張）
- Test: `tests/test_web_queries.py`, `tests/test_web.py`（既定変更に追随＋新テスト）
- Modify: `docs/superpowers/specs/2026-07-02-web-viewer-design.md`（既定値とソートキーの記載更新）

**Interfaces:**
- Consumes: Task 1-2の新列、Task 3の再構築済みDB
- Produces:
  - `queries.SORT_COLUMNS` に `"fractional_citations"` 追加（5キー）
  - `queries.ranking(session, sort="fwci_mean", min_works=1, page=1) -> tuple[list[Row], int, int]` — 3つ目は**フィルタなし**の総人数 `total_all`
  - `/` の既定 `min_works=1`、内訳表示、被引用(補正)列＋top1%列
  - `pct` Jinjaフィルタ

- [ ] **Step 1: conftest のseedを拡張**（`tests/conftest.py`）

Researcher A1 の行に `i10_index=150, two_yr_mean_citedness=3.1,` を追加（`h_index=20,` の直後）。A2/A3/A4はそのまま（新列はdefault）。

ResearcherMetrics の3行を以下に差し替え:

```python
            ResearcherMetrics(researcher_id="A1", works_count_3y=10,
                              total_citations=500, fwci_mean=3.5,
                              fwci_median=2.0, top10pct_count=4,
                              first_author_count=3, corresponding_count=5,
                              top1pct_count=2, fractional_works=4.0,
                              fractional_citations=120.5, avg_authors=5.0,
                              intl_collab_rate=0.5, corp_collab_rate=0.1,
                              oa_rate=0.7, preprint_count=2,
                              dataset_software_count=1, unique_coauthors=42,
                              top_subfield="Health Informatics",
                              computed_at=""),
            ResearcherMetrics(researcher_id="A2", works_count_3y=8,
                              total_citations=900, fwci_mean=None,
                              fwci_median=None, top10pct_count=1,
                              first_author_count=2, corresponding_count=1,
                              top1pct_count=0, fractional_works=2.0,
                              fractional_citations=300.0, avg_authors=8.0,
                              intl_collab_rate=0.25, corp_collab_rate=None,
                              oa_rate=0.5, preprint_count=0,
                              dataset_software_count=0, unique_coauthors=10,
                              top_subfield=None, computed_at=""),
            ResearcherMetrics(researcher_id="A3", works_count_3y=2,
                              total_citations=50, fwci_mean=9.9,
                              fwci_median=9.9, top10pct_count=2,
                              first_author_count=2, corresponding_count=2,
                              top1pct_count=1, fractional_works=1.0,
                              fractional_citations=25.0, avg_authors=2.0,
                              intl_collab_rate=1.0, corp_collab_rate=0.0,
                              oa_rate=1.0, preprint_count=0,
                              dataset_software_count=0, unique_coauthors=3,
                              top_subfield="ML", computed_at=""),
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_web_queries.py` の変更:

1. `ranking` は3値を返すようになる — 既存の全ての `rows, total = ranking(...)` を `rows, total, total_all = ranking(...)` に、`rows, _ = ranking(...)` を `rows, _, _ = ranking(...)` に変更
2. `test_ranking_default_filters_and_sorts` を差し替え:

```python
def test_ranking_default_filters_and_sorts(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s)
    ids = [r.Researcher.openalex_id for r in rows]
    assert ids == ["A3", "A1", "A2"]  # 既定min_works=1、NULL FWCIは末尾
    assert total == 3
    assert total_all == 3


def test_ranking_min_works_five(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, min_works=5)
    assert [r.Researcher.openalex_id for r in rows] == ["A1", "A2"]
    assert total == 2 and total_all == 3
```

3. `test_ranking_all_sort_keys` の parametrize に1行追加:

```python
    ("fractional_citations", "A2"),  # 300.0
```

`tests/test_web.py` の変更:

1. `test_ranking_page_default` を差し替え:

```python
def test_ranking_page_default(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Ichiro Tanaka" in body  # 既定min_works=1で全員表示
    assert body.index("Ichiro Tanaka") < body.index("Taro Yamada")  # fwci 9.9先頭
    assert body.index("Taro Yamada") < body.index("Hanako Suzuki")  # NULL末尾
    assert "全3人中 3人を表示" in body
    assert "最終同期: 2026-07-02" in body
    assert "OpenAlex収録分に基づく" in body
```

2. `test_ranking_sort_and_min_works` の `min_works=0` を `min_works=5` ベースの内訳アサーションに変更:

```python
def test_ranking_sort_and_min_works(client):
    body = client.get("/?sort=total_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")
    body5 = client.get("/?min_works=5").text
    assert "全3人中 2人を表示" in body5
    assert "Ichiro Tanaka" not in body5
```

3. 新テストを追加:

```python
def test_ranking_fractional_sort_and_top1_column(client):
    body = client.get("/?sort=fractional_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")  # 300>120.5
    assert "top1%" in body


def test_researcher_detail_new_metrics(client):
    body = client.get("/researchers/A1").text
    assert "国際共著率" in body and "50%" in body
    assert "産学連携率" in body and "10%" in body
    assert "Health Informatics" in body
    assert "ユニーク共著者" in body and "42" in body
    assert "i10指数" in body and "150" in body
    assert "120.50" in body  # 被引用(補正)
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web_queries.py tests/test_web.py -v`
Expected: FAIL（unpackエラー・アサーション失敗）

- [ ] **Step 4: queries実装**（`web/queries.py`）

`SORT_COLUMNS` に追加:

```python
    "fractional_citations": ResearcherMetrics.fractional_citations,
```

`ranking` を差し替え:

```python
def ranking(session, sort="fwci_mean", min_works=1, page=1):
    col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_mean)
    cond = ResearcherMetrics.works_count_3y >= min_works
    total_all = session.scalar(
        select(func.count()).select_from(ResearcherMetrics))
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
    return rows, total, total_all
```

- [ ] **Step 5: app実装**（`web/app.py`）

`_fmt` の直後に追加:

```python
def _pct(value):
    return "–" if value is None else f"{value * 100:.0f}%"
```

`create_app` 内、`templates.env.filters["fmt"] = _fmt` の直後に追加:

```python
    templates.env.filters["pct"] = _pct
```

`ranking_page` を差し替え（既定 `min_works: str = "1"`、`_int_param(min_works, 1)`、3値unpack、`total_all` をcontextへ）:

```python
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
```

- [ ] **Step 6: テンプレート更新**

`web/templates/ranking.html`:
- `<h2>` 行を差し替え: `<h2>ランキング <span class="total">全{{ total_all }}人中 {{ total }}人を表示</span></h2>`
- theadの `総被引用` th の直後に追加:

```html
  <th><a href="/?sort=fractional_citations&min_works={{ min_works }}">被引用(補正){% if sort == 'fractional_citations' %} ▼{% endif %}</a></th>
```

- theadの `top10%` th の直後に追加: `<th>top1%</th>`
- tbodyの `{{ m.total_citations }}` td の直後に追加: `<td>{{ m.fractional_citations|fmt }}</td>`
- tbodyの `{{ m.top10pct_count }}` td の直後に追加: `<td>{{ m.top1pct_count }}</td>`

`web/templates/researcher.html` の `<dl class="metrics">` 内、`h指数（全期間）` の `<div>` の直後に追加:

```html
  <div><dt>top1%論文</dt><dd>{{ m.top1pct_count }}</dd></div>
  <div><dt>被引用(補正)</dt><dd>{{ m.fractional_citations|fmt }}</dd></div>
  <div><dt>論文数(補正)</dt><dd>{{ m.fractional_works|fmt }}</dd></div>
  <div><dt>平均著者数</dt><dd>{{ m.avg_authors|fmt }}</dd></div>
  <div><dt>ユニーク共著者</dt><dd>{{ m.unique_coauthors }}</dd></div>
  <div><dt>国際共著率</dt><dd>{{ m.intl_collab_rate|pct }}</dd></div>
  <div><dt>産学連携率</dt><dd>{{ m.corp_collab_rate|pct }}</dd></div>
  <div><dt>OA率</dt><dd>{{ m.oa_rate|pct }}</dd></div>
  <div><dt>preprint</dt><dd>{{ m.preprint_count }}</dd></div>
  <div><dt>データ/SW</dt><dd>{{ m.dataset_software_count }}</dd></div>
  <div><dt>主分野</dt><dd>{{ m.top_subfield or "–" }}</dd></div>
  <div><dt>i10指数（全期間）</dt><dd>{{ r.i10_index }}</dd></div>
  <div><dt>2年平均被引用</dt><dd>{{ r.two_yr_mean_citedness|fmt }}</dd></div>
```

- [ ] **Step 7: 旧設計書の記載を更新**（`docs/superpowers/specs/2026-07-02-web-viewer-design.md`）

- `sort` の行: `` `fractional_citations` `` を選択肢に追加
- `min_works` の行: 「デフォルト5（…）。0〜1,000,000の整数、不正値・範囲外は5」を「デフォルト1。0〜1,000,000の整数、不正値・範囲外は1」に変更（括弧内の説明文は削除）

- [ ] **Step 8: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` — 全件PASS・警告なし

- [ ] **Step 9: 実DBでの表示確認**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
curl -s "http://127.0.0.1:8199/" | grep -o "全[0-9,]*人中 [0-9,]*人を表示" | head -1
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8199/?sort=fractional_citations"
kill %1
```

Expected: 「全3876人中 3338人を表示」相当の文字列と 200

- [ ] **Step 10: Commit**

```bash
git add web tests/conftest.py tests/test_web_queries.py tests/test_web.py docs/superpowers/specs/2026-07-02-web-viewer-design.md
git commit -m "feat: ランキング既定1本＋内訳表示・著者数補正/top1%列・詳細カード拡充"
```

---

## 完了条件

- `uv run pytest -m "not smoke"` 全件PASS
- 再構築済みDBに新指標が入っている（Task 3のスポットチェック合格）
- `/` が既定で3,338人規模を表示し「全3,876人中 3,338人を表示」形式の内訳が出る
- `/researchers/<id>` に国際共著率・著者数補正等の新カードが表示される
