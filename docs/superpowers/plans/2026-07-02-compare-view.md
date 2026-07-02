# 並列比較ビュー 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究者2〜4人を横並び比較する `/compare` ページと、ランキング・検索からのチェックボックス選択UIを追加する。

**Architecture:** `web/queries.py` に順序保持の `compare()`、`web/app.py` に `/compare` ルート＋行構造ビルダー `_compare_table()`（最良値ハイライトをサーバー側で計算）、`compare.html` テンプレート、`compare.js`（バニラJS・プログレッシブエンハンスメント）。

**Tech Stack:** 既存スタックのみ。新規依存なし。JSビルドなし。

**設計書:** `docs/superpowers/specs/2026-07-02-compare-view-design.md`

## Global Constraints

- `ids` パース: カンマ区切り→空白除去→重複除去（先勝ち）→不明ID無視→先頭4件。有効2人未満は案内メッセージ（200）
- 最良値ハイライト: 数値のみ対象、None除外、有効数値2個以上かつ全員同値でない場合のみ `class="best"`。「最良=最大」
- 主分野（top_subfield非NULL）が2種類以上なら分野注意文「主分野が異なります。生の被引用数ではなく FWCI 等の分野正規化済み指標での比較を推奨します」を表示
- 表示フォーマットは既存の `_fmt` / `_pct` / `_man` を再利用。氏名は `name_ja or display_name`
- JSが無効でも既存ページの機能は損なわれない（チェックボックスは無害に残る）

---

### Task 1: queries.compare

**Files:**
- Modify: `web/queries.py`
- Test: `tests/test_web_queries.py`

**Interfaces:**
- Produces: `web.queries.compare(session, ids: list[str]) -> list[Row]` — Row は `.Researcher` / `.ResearcherMetrics`（outerjoin、metrics無しはNone）。**引数idsの順序を保持**、不明IDはスキップ。空リストは空リスト

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web_queries.py` に追記。importの `ranking` 等に `compare` を追加）

```python
def test_compare_preserves_order_and_skips_unknown(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = compare(s, ["A3", "NOPE", "A1"])
        assert [r.Researcher.openalex_id for r in rows] == ["A3", "A1"]


def test_compare_outerjoin_and_empty(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = compare(s, ["A4"])
        assert rows[0].Researcher.display_name == "Jiro Sato"
        assert rows[0].ResearcherMetrics is None
        assert compare(s, []) == []
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_web_queries.py -v` → FAIL（ImportError）

- [ ] **Step 3: 実装**（`web/queries.py` の `search` の後に追加）

```python
def compare(session, ids):
    if not ids:
        return []
    rows = session.execute(
        select(Researcher, ResearcherMetrics)
        .outerjoin(ResearcherMetrics,
                   ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(Researcher.openalex_id.in_(ids))
    ).all()
    by_id = {row.Researcher.openalex_id: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_web_queries.py -v` → PASS。全suite PASS

- [ ] **Step 5: Commit**

```bash
git add web/queries.py tests/test_web_queries.py
git commit -m "feat: 比較用クエリ（順序保持・outerjoin）"
```

---

### Task 2: /compareルート＋テンプレート＋選択UI

**Files:**
- Modify: `web/app.py`（`_compare_table` ＋ `/compare` ルート）
- Create: `web/templates/compare.html`
- Create: `web/static/compare.js`
- Modify: `web/templates/base.html`（比較バー＋script読み込み）
- Modify: `web/templates/ranking.html`, `web/templates/search.html`（チェックボックス列）
- Modify: `web/static/style.css`（追記）
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `queries.compare`（Task 1）、既存フィルタ関数 `_fmt` / `_pct` / `_man`
- Produces: `GET /compare?ids=...`、各一覧ページの `input.cmp` チェックボックスと `#compare-bar`

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web.py` に追記）

```python
def test_compare_page_basic(client):
    body = client.get("/compare?ids=A1,A2").text
    assert "Taro Yamada" in body and "Hanako Suzuki" in body
    # 総被引用はA2(900)が最良
    assert '<td class="best">900</td>' in body
    # FWCI平均はA2がNone→数値1個のみ→ハイライトなし
    assert '<td class="best">3.50</td>' not in body
    # A2はtop_subfield None → 非NULLは1種類 → 注意なし
    assert "主分野が異なります" not in body
    assert "OpenAlex収録分に基づく" in body


def test_compare_order_and_subfield_warning(client):
    body = client.get("/compare?ids=A3,A1").text
    assert body.index("Ichiro Tanaka") < body.index("Taro Yamada")  # 順序保持
    assert "主分野が異なります" in body  # ML vs Health Informatics


def test_compare_metricsless_and_dedupe(client):
    body = client.get("/compare?ids=A1,A4,A1").text
    assert "佐藤次郎" in body  # metrics無しでも列は出る
    assert body.count("Taro Yamada") == 1  # 重複除去（列見出しに1回だけ）


def test_compare_insufficient_ids(client):
    for url in ("/compare", "/compare?ids=A1", "/compare?ids=,,bogus"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "2〜4人選んでください" in resp.text


def test_listing_pages_have_compare_controls(client):
    ranking = client.get("/?min_works=0").text
    assert 'class="cmp"' in ranking and 'data-id="A1"' in ranking
    assert 'id="compare-bar"' in ranking and "/static/compare.js" in ranking
    search = client.get("/search?q=yama").text
    assert 'class="cmp"' in search and 'data-id="A1"' in search
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_web.py -v` → FAIL

- [ ] **Step 3: app実装**（`web/app.py`）

`_man` の直後にモジュールレベルで追加:

```python
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
```

`create_app` 内、`search_page` の後にルート追加:

```python
    @app.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request, ids: str = ""):
        id_list: list[str] = []
        for raw_id in ids.split(","):
            rid = raw_id.strip()
            if rid and rid not in id_list:
                id_list.append(rid)
        id_list = id_list[:4]
        with Session(engine) as session:
            synced = queries.last_synced(session)
            entries = queries.compare(session, id_list)
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
```

- [ ] **Step 4: テンプレート実装**

`web/templates/compare.html`:

```html
{% extends "base.html" %}
{% block title %}研究者比較 - OMU研究者比較{% endblock %}
{% block content %}
<h2>研究者比較</h2>
{% if pairs|length < 2 %}
<p>比較するには研究者を2〜4人選んでください。<a href="/">ランキング</a>や<a href="/search">検索</a>の行頭のチェックボックスで選択し、「比較する」ボタンを押してください。</p>
{% else %}
{% if subfield_warning %}
<p class="notice">主分野が異なります。生の被引用数ではなく FWCI 等の分野正規化済み指標での比較を推奨します。</p>
{% endif %}
<table class="compare">
<thead><tr><th></th>
{% for r, m in pairs %}
  <th><a href="/researchers/{{ r.openalex_id }}">{{ r.name_ja or r.display_name }}</a></th>
{% endfor %}
</tr></thead>
<tbody>
{% for group in groups %}
<tr class="group"><th colspan="{{ pairs|length + 1 }}">{{ group.label }}</th></tr>
{% for row in group.rows %}
<tr>
  <th>{{ row.label }}</th>
  {% for cell in row.cells %}
  <td{% if cell.best %} class="best"{% endif %}>{{ cell.text }}</td>
  {% endfor %}
</tr>
{% endfor %}
{% endfor %}
</tbody>
</table>
{% endif %}
{% endblock %}
```

`web/templates/base.html`: `<footer>` の直前に追加:

```html
<div id="compare-bar" hidden>
  <span id="compare-names"></span>
  <button id="compare-go" disabled>比較する</button>
</div>
<script src="/static/compare.js"></script>
```

`web/templates/ranking.html`: theadの `<th>#</th>` の直前に `<th></th>` を追加し、tbodyの順位 `<td>` の直前に追加:

```html
  <td><input type="checkbox" class="cmp" data-id="{{ r.openalex_id }}" data-name="{{ r.name_ja or r.display_name }}"></td>
```

`web/templates/search.html`: 結果表のtheadの `<th>氏名</th>` の直前に `<th></th>` を追加し、tbodyの氏名 `<td>` の直前に同じチェックボックス行を追加（`{{ r.openalex_id }}` / `{{ r.name_ja or r.display_name }}` は同一）。

`web/static/compare.js`:

```javascript
(function () {
  var bar = document.getElementById("compare-bar");
  if (!bar) return;
  var names = document.getElementById("compare-names");
  var go = document.getElementById("compare-go");

  function selected() {
    return Array.prototype.slice.call(
      document.querySelectorAll("input.cmp:checked"));
  }

  function update() {
    var sel = selected();
    bar.hidden = sel.length === 0;
    names.textContent = sel.map(function (el) {
      return el.dataset.name;
    }).join("、");
    go.disabled = sel.length < 2 || sel.length > 4;
    go.textContent = "比較する（" + sel.length + "人）";
  }

  document.addEventListener("change", function (e) {
    if (e.target.classList && e.target.classList.contains("cmp")) update();
  });

  go.addEventListener("click", function () {
    var ids = selected().map(function (el) { return el.dataset.id; });
    window.location.href = "/compare?ids=" + ids.join(",");
  });
})();
```

`web/static/style.css` に追記:

```css
.notice { background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; padding: 0.6rem 0.9rem; font-size: 0.9rem; }
table.compare th:first-child { width: 11rem; }
td.best { font-weight: 700; background: #e8f4ea; }
tr.group th { background: #e5e7eb; font-size: 0.8rem; letter-spacing: 0.05em; }
#compare-bar { position: fixed; bottom: 0; left: 0; right: 0; background: var(--accent); color: #fff; padding: 0.6rem 1.5rem; display: flex; gap: 1rem; align-items: center; z-index: 10; }
#compare-bar button { padding: 0.4rem 1rem; border: none; border-radius: 4px; background: #fff; color: var(--accent); font-weight: 600; cursor: pointer; }
#compare-bar button:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 6: 実DBで表示確認**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
IDS=$(curl -s "http://127.0.0.1:8199/" | grep -oP 'researchers/\KA[0-9]+' | head -2 | paste -sd,)
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8199/compare?ids=${IDS}"   # 200
curl -s "http://127.0.0.1:8199/compare?ids=${IDS}" | grep -c 'class="best"'          # 1以上
kill %1
```

Expected: 200 / 1以上（実データでハイライトが出る）

- [ ] **Step 7: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: 並列比較ビュー（/compare・チェックボックス選択・最良値ハイライト）"
```

---

## 完了条件

- `uv run pytest -m "not smoke"` 全件PASS
- 実DBで `/compare?ids=<実ID2つ>` が表示され、最良値ハイライトと（分野が違えば）注意文が出る
- ランキング・検索にチェックボックスと比較バーが出て、JS無効でも既存機能が壊れない
