# FWCI合計＋指標別学内順位 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FWCI合計（ΣFWCI）を追加してデフォルトソートにし、研究者詳細ページに8指標の学内順位を表示する。

**Architecture:** metrics層に `fwci_total` 集計、queries層に `metric_ranks()`（オンザフライCOUNT）、web層でデフォルトソート切替＋列＋順位表示。DBは確立済みの再構築運用で反映。

**Tech Stack:** 既存スタックのみ。

**設計書:** `docs/superpowers/specs/2026-07-02-fwci-total-ranks-design.md`

## Global Constraints

- `fwci_total` = ウィンドウ内worksの非NULL FWCIの合計、小数4桁丸め、0本なら0
- デフォルトソートは `fwci_total`（queries.rankingのデフォルト引数・appのフォールバック・テンプレートの3箇所を一致させる）
- 順位: 母数 = `works_count_3y >= 1`。順位 = `1 + COUNT(指標 > 本人値)`（同値同順位）。本人値NULLまたは母数外は非表示（Noneを返す）
- 順位対象8指標のキー: `works_count_3y` / `total_citations` / `fractional_citations` / `fwci_total` / `fwci_mean` / `top10pct_count` / `top1pct_count` / `kaken_total_amount`
- 表示文言は「学内◯位」（例: 学内12位）。スタイルは値の下に小さく

---

### Task 1: fwci_total（モデル＋集計）

**Files:**
- Modify: `db/models.py`（ResearcherMetricsに1列）
- Modify: `collector/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `ResearcherMetrics.fwci_total: Mapped[float] = mapped_column(Float, default=0)`（`fwci_median` 行の直後に追加）。compute_metricsが集計

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_metrics.py` の `test_compute_metrics` に追記）

m1ブロックの `assert m1.fwci_median == 1.5` の直後:

```python
        assert m1.fwci_total == 3.0  # 2.0 + 1.0（W4のNULLは除外）
```

m2ブロック（論文ゼロ）の `assert m2.fwci_mean is None` の直後:

```python
        assert m2.fwci_total == 0
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_metrics.py -v` → FAIL（AttributeError または AssertionError）

- [ ] **Step 3: 実装**

`db/models.py` `ResearcherMetrics` の `fwci_median` 行の直後に:

```python
    fwci_total: Mapped[float] = mapped_column(Float, default=0)
```

`collector/metrics.py` の `ResearcherMetrics(...)` 呼び出しで `fwci_median=...` 行の直後に:

```python
            fwci_total=round(sum(fwcis), 4) if fwcis else 0,
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_metrics.py -v` → PASS。全suite PASS

- [ ] **Step 5: Commit**

```bash
git add db/models.py collector/metrics.py tests/test_metrics.py
git commit -m "feat: FWCI合計（ΣFWCI）をメトリクスに追加"
```

---

### Task 2: queries（デフォルトソート切替＋metric_ranks）

**Files:**
- Modify: `web/queries.py`
- Modify: `tests/conftest.py`（metricsに fwci_total をseed）
- Test: `tests/test_web_queries.py`

**Interfaces:**
- Consumes: Task 1の `fwci_total` 列
- Produces:
  - `SORT_COLUMNS` に `"fwci_total"` 追加（8キー）
  - `ranking(session, sort="fwci_total", min_works=1, page=1)` — デフォルトソート変更
  - `web.queries.RANK_METRICS: dict[str, Column]` — 順位対象8指標
  - `web.queries.metric_ranks(session, researcher_id) -> dict[str, tuple[int, int]] | None` — 本人が母数外（works_count_3y < 1 またはmetrics行なし）なら None。各指標: 本人値NULLならdictから除外。値は `(順位, 母数)`

- [ ] **Step 1: conftest seed拡張**

ResearcherMetrics の3行に追加: A1 `fwci_total=35.0,`、A2 `fwci_total=0,`、A3 `fwci_total=19.8,`（いずれも `fwci_median=...` の直後）。

- [ ] **Step 2: 失敗するテストを書く**（`tests/test_web_queries.py`）

importに `metric_ranks` を追加。`test_ranking_default_filters_and_sorts` を差し替え:

```python
def test_ranking_default_filters_and_sorts(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s)
    ids = [r.Researcher.openalex_id for r in rows]
    assert ids == ["A1", "A3", "A2"]  # 既定=fwci_total降順: 35.0, 19.8, 0
    assert total == 3
    assert total_all == 3
```

`test_ranking_all_sort_keys` の parametrize に追加:

```python
    ("fwci_total", "A1"),  # 35.0
```

新規テスト:

```python
def test_metric_ranks(seeded_db_path):
    with _session(seeded_db_path) as s:
        ranks = metric_ranks(s, "A2")
        # 母数はworks>=1の3人
        assert ranks["total_citations"] == (1, 3)   # 900は最大
        assert ranks["fwci_total"] == (3, 3)        # 0は最下位
        assert "fwci_mean" not in ranks             # 本人値NULLは非表示
        ranks1 = metric_ranks(s, "A1")
        assert ranks1["fwci_mean"] == (2, 3)        # 9.9(A3) > 3.5(A1) > NULL
        assert ranks1["kaken_total_amount"] == (1, 3)


def test_metric_ranks_outside_population(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert metric_ranks(s, "A4") is None    # metrics行なし
        assert metric_ranks(s, "NOPE") is None
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web_queries.py -v` → FAIL

- [ ] **Step 4: 実装**（`web/queries.py`）

`SORT_COLUMNS` に追加:

```python
    "fwci_total": ResearcherMetrics.fwci_total,
```

`ranking` のシグネチャを `def ranking(session, sort="fwci_total", min_works=1, page=1):` に変更し、フォールバック行を `col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_total)` に変更。

`search` の後に追加:

```python
RANK_METRICS = {
    "works_count_3y": ResearcherMetrics.works_count_3y,
    "total_citations": ResearcherMetrics.total_citations,
    "fractional_citations": ResearcherMetrics.fractional_citations,
    "fwci_total": ResearcherMetrics.fwci_total,
    "fwci_mean": ResearcherMetrics.fwci_mean,
    "top10pct_count": ResearcherMetrics.top10pct_count,
    "top1pct_count": ResearcherMetrics.top1pct_count,
    "kaken_total_amount": ResearcherMetrics.kaken_total_amount,
}


def metric_ranks(session, researcher_id):
    own = session.get(ResearcherMetrics, researcher_id)
    if own is None or own.works_count_3y < 1:
        return None
    population = ResearcherMetrics.works_count_3y >= 1
    total = session.scalar(
        select(func.count()).select_from(ResearcherMetrics).where(population))
    ranks: dict[str, tuple[int, int]] = {}
    for key, col in RANK_METRICS.items():
        value = getattr(own, key)
        if value is None:
            continue
        higher = session.scalar(
            select(func.count()).select_from(ResearcherMetrics)
            .where(population, col > value))
        ranks[key] = (higher + 1, total)
    return ranks
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_web_queries.py -v` → PASS。全suite PASS
（注: `tests/test_web.py` の既存テストがデフォルトソート変更で落ちる場合があるが、その修正はTask 3で行う。このタスクの時点で落ちるのが `test_web.py` のソート順アサーションのみであることを確認し、報告に記載）

- [ ] **Step 6: Commit**

```bash
git add web/queries.py tests/conftest.py tests/test_web_queries.py
git commit -m "feat: デフォルトソートをFWCI合計へ・指標別学内順位クエリ"
```

---

### Task 3: web表示（列・順位・比較行）

**Files:**
- Modify: `web/app.py`（デフォルト・順位context・比較行）
- Modify: `web/templates/ranking.html`, `researcher.html`
- Modify: `web/static/style.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: Task 2の `metric_ranks` / SORT_COLUMNS
- Produces: `/` デフォルトFWCI合計・FWCI合計列、詳細ページの「学内◯位」、比較ビューのFWCI合計行

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web.py`）

`test_ranking_page_default` のソート順アサーション2行を差し替え:

```python
    assert body.index("Taro Yamada") < body.index("Ichiro Tanaka")   # fwci_total 35.0 > 19.8
    assert body.index("Ichiro Tanaka") < body.index("Hanako Suzuki") # 19.8 > 0
```

新規テスト:

```python
def test_ranking_fwci_total_column_and_default(client):
    body = client.get("/").text
    assert "FWCI合計" in body
    assert "FWCI合計 ▼" in body  # 既定ソートのマーカー


def test_researcher_detail_ranks(client):
    body = client.get("/researchers/A1").text
    assert "学内1位" in body   # kaken_total_amount 75M は1位
    assert "学内2位" in body   # fwci_mean 3.5 は9.9に次ぐ2位
    body4 = client.get("/researchers/A4").text
    assert "学内" not in body4  # metrics無し→順位非表示


def test_compare_has_fwci_total_row(client):
    body = client.get("/compare?ids=A1,A3").text
    assert "FWCI合計" in body
    assert '<td class="best">35.00</td>' in body
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_web.py -v` → FAIL

- [ ] **Step 3: app実装**（`web/app.py`）

1. `ranking_page` のシグネチャを `sort: str = "fwci_total"` に、フォールバック行を `sort_key = sort if sort in queries.SORT_COLUMNS else "fwci_total"` に変更
2. `researcher_page` 内、`result = queries.researcher_detail(...)` の直後に追加:

```python
            ranks = queries.metric_ranks(session, openalex_id)
```

context に `"ranks": ranks or {},` を追加
3. `_compare_table` のインパクトグループ、FWCI平均行の直前に追加:

```python
            ("FWCI合計", metric("fwci_total"), _fmt, True),
```

- [ ] **Step 4: テンプレート実装**

`web/templates/ranking.html`:
- theadの「被引用(補正)」thの直後に追加:

```html
  <th><a href="/?sort=fwci_total&min_works={{ min_works }}">FWCI合計{% if sort == 'fwci_total' %} ▼{% endif %}</a></th>
```

- tbodyの `{{ m.fractional_citations|fmt }}` tdの直後に追加: `<td>{{ m.fwci_total|fmt }}</td>`

`web/templates/researcher.html` のメトリクスカード:
- FWCI平均の `<div>` の直前に追加:

```html
  <div><dt>FWCI合計</dt><dd>{{ m.fwci_total|fmt }}{% if ranks.fwci_total %}<span class="rank">学内{{ ranks.fwci_total[0] }}位</span>{% endif %}</dd></div>
```

- 順位対象の既存7項目のdd内に同形式で追記（対応: 3年論文数→`ranks.works_count_3y`、総被引用数→`ranks.total_citations`、被引用(補正)→`ranks.fractional_citations`、FWCI平均→`ranks.fwci_mean`、top10%論文→`ranks.top10pct_count`、top1%論文→`ranks.top1pct_count`、科研費配分総額→`ranks.kaken_total_amount`）。例（FWCI平均）:

```html
  <div><dt>FWCI平均</dt><dd>{{ m.fwci_mean|fmt }}{% if ranks.fwci_mean %}<span class="rank">学内{{ ranks.fwci_mean[0] }}位</span>{% endif %}</dd></div>
```

`web/static/style.css` に追記:

```css
.metrics dd .rank { display: block; font-size: 0.7rem; font-weight: normal; color: var(--muted); }
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 6: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: FWCI合計を既定ソートに・詳細ページに学内順位を表示"
```

---

### Task 4: DB再構築＋実表示確認

**Files:** 実行のみ

- [ ] **Step 1: 再構築（実API・数分、timeout 600000ms）**

```bash
rm -f db/researchers.db db/researchers.db-wal db/researchers.db-shm
uv run python scripts/sync.py
```

Expected: `done: authors=~3876 works=~6200 metrics=~3876`＋`kaken: grants=~2119 matched=~1171`、警告なし

- [ ] **Step 2: スポットチェック**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
print('fwci_total>0:', c.execute('SELECT COUNT(*) FROM researcher_metrics WHERE fwci_total > 0').fetchone())
print('top5:', c.execute('SELECT r.display_name, m.fwci_total, m.works_count_3y FROM researcher_metrics m JOIN researchers r ON r.openalex_id=m.researcher_id ORDER BY m.fwci_total DESC LIMIT 5').fetchall())
"
```

Expected: fwci_total>0 が3,000人前後、top5に妥当な値（数十〜数百）

- [ ] **Step 3: 実表示確認（port 8199）**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
curl -s "http://127.0.0.1:8199/" | grep -c "FWCI合計 ▼"
ID=$(curl -s "http://127.0.0.1:8199/" | grep -oP 'researchers/\KA[0-9]+' | head -1)
curl -s "http://127.0.0.1:8199/researchers/${ID}" | grep -oE "学内[0-9,]+位" | head -3
kill %1
```

Expected: マーカー1以上、学内順位が表示される

- [ ] **Step 4: コミット対象なし（実行のみ）を確認**

```bash
git status --short   # 空であること（DBはgitignore）
```

---

## 完了条件

- 全テストPASS。`/` の既定がFWCI合計降順、詳細に学内順位、比較にFWCI合計行
- 実DBで順位表示が出る
