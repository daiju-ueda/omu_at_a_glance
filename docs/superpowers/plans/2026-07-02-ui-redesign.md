# UI/UX全面改善 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ランキングの2段ヘッダ＋ソート列分布バー、詳細ページのセクション再編、比較のsticky化、全体のタイポ・トークン刷新で可読性を上げる。

**Architecture:** サーバー追加ロジックはバー%計算のみ。テンプレは構造・クラス付与（**文言不変**）。style.cssは全面書き換え（トークン設計）。ビルドなし・CSS1枚・素JS構成は維持。

**Tech Stack:** 既存のみ。

**設計書:** `docs/superpowers/specs/2026-07-02-ui-redesign-design.md`

## Global Constraints

- **既存137テストを無修正で全pass**させること（文言・クラス・マーカー互換）。特に: `class="best"` / `class="cmp"` / `id="compare-bar"` / `class="rank"`（span） / dt・thラベル文言 / 「▼」 / 「全X人中 Y人を表示」 / フッターの各センテンス文字列 / `data-id` / `aria-label`
- フッターは**センテンス単位で `<li>` に分割**（各文の文字列は一字も変えない）
- 新規クラス命名: `.num`（右揃え数値）/ `.num.sub`（二次数値）/ `.sorted`（ソート列）/ `.cellbar` / `.toolbar` / `.identity` / `.chip` / `.metric-section` / `.stat-hero` / `.groups-row`
- バー: サーバー計算（0-100の整数%、値>0のみ、ページ内最大値比、最小2%）。JS追加禁止
- インラインstyleは `--w` カスタムプロパティのみ許可

---

### Task 1: ランキング（バー計算＋2段ヘッダ＋ツールバー）

**Files:**
- Modify: `web/app.py`（`_bar_widths` 追加＋ranking contextに `bars`）
- Modify: `web/templates/ranking.html`
- Test: `tests/test_web.py`（新規アサーション）, `tests/test_web_queries.py` は変更なし

**Interfaces:**
- Produces: `web.app._bar_widths(rows, sort_key) -> dict[str, int]`（researcher_id→2..100）。ranking.html は `sort` / `bars` を使い、ソート中の数値セルに `.sorted`＋`.cellbar` を出す

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web.py` に追加）

```python
def test_ranking_sorted_column_bars(client):
    body = client.get("/?min_works=0").text  # 既定sort=fwci_total
    assert 'class="num sorted"' in body
    assert "--w:" in body                      # 分布バー
    # 最大値(A1=35.0)の行は100%
    assert "--w: 100%" in body
    body2 = client.get("/?sort=total_citations&min_works=0").text
    assert 'class="num sorted"' in body2


def test_ranking_group_header_and_toolbar(client):
    body = client.get("/").text
    assert 'class="groups-row"' in body
    assert "生産性" in body and "インパクト" in body and "資金" in body
    assert 'class="toolbar"' in body
```

`web/app.py` の純関数テスト（`tests/test_web.py` 末尾）:

```python
def test_bar_widths_pure():
    from web.app import _bar_widths

    class R:
        def __init__(self, i):
            self.openalex_id = i

    class M:
        def __init__(self, v):
            self.fwci_total = v

    class Row:
        def __init__(self, i, v):
            self.Researcher = R(i)
            self.ResearcherMetrics = M(v)

    rows = [Row("A", 200.0), Row("B", 1.0), Row("C", 0), Row("D", None)]
    bars = _bar_widths(rows, "fwci_total")
    assert bars["A"] == 100
    assert bars["B"] == 2        # 最小2%
    assert "C" not in bars and "D" not in bars
    assert _bar_widths([], "fwci_total") == {}
```

- [ ] **Step 2: 失敗を確認** → FAIL

- [ ] **Step 3: app実装**（`web/app.py`）

`_int_param` の後に:

```python
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
```

`ranking_page` の context に `"bars": _bar_widths(rows, sort_key),` を追加。

- [ ] **Step 4: ranking.html再構成**

現行ファイルを読み、以下の構造に組み替える（**リンクhref・ラベル・▼・件数表示・checkbox属性は現行文字列を保持**）:

1. h2はそのまま。controlsフォームを `<div class="toolbar">` で包み、部局セレクト・最低論文数・ボタンを整理（フォーム構造・name属性不変）
2. theadを2段に。1段目（グループ行）:

```html
<tr class="groups-row">
  <th colspan="2"></th><th></th>
  <th colspan="2" class="group">生産性</th>
  <th colspan="6" class="group">インパクト</th>
  <th colspan="2" class="group">著者性</th>
  <th class="group">資金</th>
</tr>
```

（列の対応: 選択+# / 氏名 / 論文数・被引用(補正)?——**現行の列順を先に確認し、colspanを実際の列構成に一致させること**。生産性=論文数、インパクト=総被引用〜top1%、著者性=筆頭・責任、資金=科研費総額。合計が下段のth数と一致しない場合は必ず数え直す）

3. 下段ヘッダ: 各ソート可能thに `{% if sort == '<key>' %} class="sorted"{% endif %}`（既存の▼はそのまま）
4. tbody: Jinjaマクロで数値セルを共通化:

```jinja
{% macro numcell(key, rid, text, sub=False) -%}
<td class="num{% if sub %} sub{% endif %}{% if sort == key %} sorted{% endif %}">{% if sort == key and bars.get(rid) %}<span class="cellbar" style="--w: {{ bars[rid] }}%"></span>{% endif %}{{ text }}</td>
{%- endmacro %}
```

ソート可能列（works_count_3y / total_citations / fractional_citations / fwci_total / fwci_mean / top10pct_count / kaken_total_amount）は `numcell(...)`、非ソート数値列（fwci_median・top1pct_count・筆頭・責任）は `<td class="num sub">…</td>`。**表示フォーマット（fmt/man/桁区切り）は現行のまま**

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → **既存137＋新3が全pass・無修正**

- [ ] **Step 6: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: ランキングに2段ヘッダとソート列分布バー"
```

---

### Task 2: 詳細・比較・部局・共通テンプレ

**Files:**
- Modify: `web/templates/researcher.html`, `compare.html`, `departments.html`, `base.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: 詳細のアイデンティティブロック＋4セクション、比較のsticky用クラス、フッターのli分割、navの現在地

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web.py`）

```python
def test_detail_identity_and_sections(client):
    body = client.get("/researchers/A1").text
    assert 'class="identity"' in body
    for heading in ("インパクト", "生産性", "連携・資金", "研究者指標・実績（全期間）"):
        assert heading in body
    assert 'class="metric-section"' in body
    assert 'class="chip"' in body   # OpenAlex/ORCIDリンク


def test_footer_is_list(client):
    body = client.get("/").text
    assert "データについて" in body
    assert body.count("<li>") >= 4  # 注記の文分割
```

- [ ] **Step 2: 失敗を確認** → FAIL

- [ ] **Step 3: researcher.html再構成**

現行を読み、以下へ（**全dtラベル・値の式・rankスパン・受賞リストの文言は不変**。並べ替えとセクション化のみ）:

```html
<div class="identity">
  <h2>{{ r.name_ja or r.display_name }}</h2>
  {% if r.name_ja %}<p class="alt-name">{{ r.display_name }}</p>{% endif %}
  {% if r.department %}<p class="affil">{{ r.department }}{% if r.position %}・{{ r.position }}{% endif %}</p>{% endif %}
  <p class="links">
    <a class="chip" href="https://openalex.org/{{ r.openalex_id }}" target="_blank">OpenAlex</a>
    {% if r.orcid %}<a class="chip" href="https://orcid.org/{{ r.orcid }}" target="_blank">ORCID</a>{% endif %}
  </p>
</div>
```

メトリクスは4つの `<section class="metric-section">`（`<h3>` 見出し: インパクト / 生産性 / 連携・資金 / 研究者指標・実績（全期間））に分配。設計書の割当に従う。各セクション内は既存の `<dl class="metrics">` 構造・div/dt/dd記法を維持。**FWCI合計・総被引用数・3年論文数・科研費配分総額**の4カードに `class="stat-hero"` を div に追加（例: `<div class="stat-hero"><dt>FWCI合計</dt>...`）。
メトリクス未計算メッセージ・受賞歴・論文リストは現行のまま（受賞リストのulに `class="awards"` は既存）。

- [ ] **Step 4: compare / departments / base**

`compare.html`: `<table class="compare">` に `class="compare sticky"` を付与（または既存クラスに `sticky` 追加）。グループ行 `<tr class="group">` は既存。変更はクラスのみ。
`departments.html`: 数値tdに `class="num"` を付与、人あたりFWCI合計の `<strong>` を `<strong class="accent">` に。
`base.html`:
- navの各リンクに現在地クラス: `{% set path = request.url.path %}` を使い `<a href="/" {% if path == '/' %}class="current"{% endif %}>ランキング</a>` 形式（4リンク全て。/researchers配下はランキング扱いにしない——単純にpath完全一致のみ）
- ヘッダタイトルの下に `<span class="tagline">Research Metrics</span>`
- フッター: 現在の連結文を読み、**センテンス（「。」区切り）単位で分割**して以下の構造に（各文の文字列は句点含め不変。「注: 」は最初のliの先頭に残してよい）:

```html
<footer>
  <p class="foot-title">データについて</p>
  <ul>
    <li>OpenAlex収録分に基づく（直近3年ローリング）{% if synced %}・最終同期: {{ synced }}{% endif %}</li>
    <li>（以下、現行の注記文を1文ずつ）</li>
  </ul>
</footer>
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全pass（既存無修正）

- [ ] **Step 6: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: 詳細のセクション再編・比較sticky・フッター整理"
```

---

### Task 3: style.css全面書き換え

**Files:**
- Replace: `web/static/style.css`（下記の完全な内容で置換）

**Interfaces:**
- Consumes: Task 1-2のクラス
- Produces: 新デザインシステム一式

- [ ] **Step 1: style.cssを以下の内容で全置換**

```css
/* ===== tokens ===== */
:root {
  --paper: #f6f6f2;
  --panel: #ffffff;
  --ink: #1c2430;
  --muted: #67707e;
  --line: #e2e3dd;
  --line-strong: #c9cbc2;
  --indigo: #274c77;
  --indigo-deep: #16324f;
  --indigo-tint: #eef2f7;
  --gold: #a97e2f;
  --gold-tint: #f7f1e3;
  --bar: #b9c9dc;
  --radius: 6px;
}

/* ===== base ===== */
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Hiragino Sans", "Noto Sans JP", "Yu Gothic UI", system-ui, sans-serif;
  color: var(--ink);
  background: var(--paper);
  font-size: 15px;
  line-height: 1.65;
}
a { color: var(--indigo); }
:focus-visible { outline: 2px solid var(--indigo); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}

/* ===== header ===== */
header {
  display: flex; align-items: baseline; gap: 1.6rem;
  padding: 0.85rem 1.6rem;
  background: var(--indigo-deep);
}
header h1 { font-size: 1.05rem; margin: 0; letter-spacing: 0.02em; }
header h1 a { color: #fff; text-decoration: none; }
header .tagline {
  color: #8ba3bd; font-size: 0.68rem; letter-spacing: 0.22em;
  margin-left: 0.6rem;
}
header nav { display: flex; gap: 1.1rem; }
header nav a {
  color: #c7d5e4; text-decoration: none; font-size: 0.88rem;
  padding-bottom: 2px; border-bottom: 2px solid transparent;
  transition: color 0.15s;
}
header nav a:hover { color: #fff; }
header nav a.current { color: #fff; border-bottom-color: var(--gold); }

main { max-width: 1160px; margin: 0 auto; padding: 1.6rem; }

/* ===== headings & toolbar ===== */
h2 {
  font-size: 1.2rem; margin: 0.2rem 0 0.9rem;
  padding-bottom: 0.4rem; border-bottom: 2px solid var(--line-strong);
}
h2 .total { font-size: 0.82rem; color: var(--muted); font-weight: normal; margin-left: 0.6rem; }
h3 { font-size: 0.95rem; margin: 1.4rem 0 0.5rem; color: var(--indigo-deep); }
.toolbar {
  display: flex; flex-wrap: wrap; gap: 0.8rem; align-items: center;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 0.55rem 0.9rem; margin: 0 0 1rem; font-size: 0.88rem;
}
.toolbar label { display: flex; align-items: center; gap: 0.4rem; color: var(--muted); }
.toolbar select, .toolbar input, .toolbar button {
  padding: 0.3rem 0.55rem; border: 1px solid var(--line-strong);
  border-radius: 4px; font-size: 0.88rem; background: #fff; color: var(--ink);
}
.toolbar input[type="number"] { width: 4.6rem; }
.toolbar button {
  background: var(--indigo); color: #fff; border-color: var(--indigo); cursor: pointer;
}

/* ===== tables ===== */
table {
  width: 100%; border-collapse: collapse; background: var(--panel);
  font-size: 0.875rem; border: 1px solid var(--line);
}
th, td { padding: 0.42rem 0.65rem; border-bottom: 1px solid var(--line); text-align: left; }
thead th {
  background: #f1f2ec; font-size: 0.8rem; color: var(--indigo-deep);
  position: sticky; top: 0; z-index: 2; white-space: nowrap;
}
thead th a { text-decoration: none; }
tr.groups-row th {
  background: var(--indigo-deep); color: #d9e2ec;
  font-size: 0.7rem; letter-spacing: 0.18em; font-weight: normal;
  border-bottom: none; padding: 0.28rem 0.65rem;
}
tbody tr:hover { background: var(--indigo-tint); }
td a { color: var(--indigo); }

/* 数値セル */
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.num.sub { color: var(--muted); }

/* ソート列＋分布バー（シグネチャ） */
thead th.sorted { border-bottom: 2px solid var(--indigo); }
thead th.sorted a { color: var(--indigo); font-weight: 700; }
td.sorted { background: var(--indigo-tint); font-weight: 600; position: relative; }
.cellbar {
  position: absolute; left: 0; bottom: 0; height: 3px;
  width: var(--w, 0%); background: var(--bar);
}

/* バッジ・チップ */
.badge {
  display: inline-block; font-size: 0.68rem; padding: 0.05rem 0.45rem;
  border-radius: 999px; margin-left: 0.4rem; vertical-align: middle;
}
.badge.top1 { background: #7c2d12; color: #fff; }
.badge.top10 { background: #b45309; color: #fff; }
.chip {
  display: inline-block; font-size: 0.8rem; padding: 0.18rem 0.75rem;
  border: 1px solid var(--indigo); border-radius: 999px;
  text-decoration: none; margin-right: 0.5rem;
  transition: background 0.15s, color 0.15s;
}
.chip:hover { background: var(--indigo); color: #fff; }

/* ===== 詳細ページ ===== */
.identity {
  background: var(--panel); border: 1px solid var(--line);
  border-left: 4px solid var(--indigo); border-radius: var(--radius);
  padding: 1rem 1.3rem; margin-bottom: 1.2rem;
}
.identity h2 { border: none; margin: 0; padding: 0; font-size: 1.45rem; }
.identity .alt-name { margin: 0.1rem 0 0; color: var(--muted); font-size: 0.9rem; }
.identity .affil { margin: 0.35rem 0 0.55rem; color: var(--ink); font-size: 0.92rem; }
.identity .links { margin: 0.3rem 0 0; }
.affil { color: var(--muted); }

.metric-section { margin-bottom: 1.1rem; }
.metric-section h3 {
  margin: 0 0 0.45rem; font-size: 0.8rem; letter-spacing: 0.14em;
  color: var(--muted);
}
.metrics {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 0.55rem; margin: 0;
}
.metrics div {
  background: var(--panel); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 0.55rem 0.75rem;
}
.metrics div.stat-hero { border-color: var(--indigo); border-width: 1.5px; }
.metrics div.stat-hero dd { font-size: 1.45rem; color: var(--indigo-deep); }
.metrics dt { font-size: 0.72rem; color: var(--muted); }
.metrics dd { margin: 0; font-size: 1.15rem; font-weight: 650; font-variant-numeric: tabular-nums; }
.metrics dd .rank {
  display: inline-block; font-size: 0.66rem; font-weight: normal;
  background: var(--indigo-tint); color: var(--indigo-deep);
  border-radius: 999px; padding: 0 0.5rem; margin-left: 0.4rem;
  vertical-align: middle;
}

ul.awards {
  list-style: none; background: var(--panel); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 0.6rem 1rem; margin: 0.4rem 0 1.2rem;
}
ul.awards li {
  padding: 0.35rem 0.2rem; border-bottom: 1px dashed var(--line);
  display: flex; justify-content: space-between; gap: 1rem;
}
ul.awards li:last-child { border-bottom: none; }
ul.awards li::before { content: "◆"; color: var(--gold); margin-right: 0.5rem; font-size: 0.7rem; }
ul.awards .year { color: var(--muted); font-size: 0.85rem; white-space: nowrap; }

/* ===== 比較 ===== */
table.compare th:first-child { width: 11rem; }
table.compare thead th { z-index: 3; }
table.compare.sticky tbody th,
table.compare.sticky td:first-child { position: sticky; left: 0; background: var(--panel); }
table.compare.sticky thead tr th:first-child { left: 0; z-index: 4; }
tr.group th {
  background: var(--indigo-tint); color: var(--indigo-deep);
  font-size: 0.75rem; letter-spacing: 0.12em;
}
td.best {
  font-weight: 700; background: var(--gold-tint);
  box-shadow: inset 3px 0 0 var(--gold);
}
.notice {
  background: #fdf6e8; border: 1px solid var(--gold); border-radius: var(--radius);
  padding: 0.6rem 0.9rem; font-size: 0.88rem; margin-bottom: 0.8rem;
}

/* ===== 部局 ===== */
strong.accent { color: var(--indigo-deep); }
table.small-depts { opacity: 0.65; }

/* ===== 比較バー（画面下） ===== */
#compare-bar {
  position: fixed; bottom: 0; left: 0; right: 0;
  background: var(--indigo-deep); color: #fff;
  padding: 0.6rem 1.6rem; display: flex; gap: 1rem; align-items: center; z-index: 10;
}
#compare-bar button {
  padding: 0.4rem 1.1rem; border: none; border-radius: 4px;
  background: var(--gold); color: #fff; font-weight: 600; cursor: pointer;
}
#compare-bar button:disabled { opacity: 0.45; cursor: default; }
body:has(input.cmp:checked) main { padding-bottom: 4rem; }

/* ===== ページャ・フッター ===== */
.pager { margin: 1rem 0; display: flex; gap: 1.2rem; font-size: 0.9rem; }
footer {
  max-width: 1160px; margin: 1.4rem auto 2.2rem; padding: 0 1.6rem;
  color: var(--muted); font-size: 0.78rem;
}
footer .foot-title {
  font-size: 0.72rem; letter-spacing: 0.16em; color: var(--muted);
  border-top: 1px solid var(--line-strong); padding-top: 0.7rem; margin: 0 0 0.3rem;
}
footer ul { margin: 0; padding-left: 1.1rem; }
footer li { margin-bottom: 0.15rem; }

/* ===== responsive ===== */
@media (max-width: 900px) {
  main { padding: 1rem; }
  table { display: block; overflow-x: auto; }
  .metrics { grid-template-columns: repeat(auto-fill, minmax(128px, 1fr)); }
  header { flex-wrap: wrap; gap: 0.6rem; }
}
```

- [ ] **Step 2: 全テスト確認**

Run: `uv run pytest -m "not smoke"` → 全pass（CSSはテスト対象外だが回 regression確認）

- [ ] **Step 3: 実DBで構造確認（port 8199）**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
for p in "/" "/?sort=total_citations" "/departments" "/compare" "/search?q=ueda"; do
  curl -s -o /dev/null -w "%{http_code} $p\n" "http://127.0.0.1:8199$p"; done
curl -s "http://127.0.0.1:8199/" | grep -c "cellbar"       # 多数
ID=$(curl -s "http://127.0.0.1:8199/" | grep -oP 'researchers/\KA[0-9]+' | head -1)
curl -s "http://127.0.0.1:8199/researchers/${ID}" | grep -c "metric-section"   # 4
kill %1
```

Expected: 全200、cellbar多数、metric-section=4

- [ ] **Step 4: Commit**

```bash
git add web/static/style.css
git commit -m "feat: デザイントークン刷新（紙×藍×金・tabular-nums・分布バー）"
```

---

## 完了条件

- 既存137テストが無修正で全pass＋新テスト
- ランキングにグループヘッダ・ソート列バーが実データで表示され、詳細が4セクション構成になる
