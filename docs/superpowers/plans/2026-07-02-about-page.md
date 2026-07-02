# 指標説明ページ 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/about` に全指標の定義・データソース・限界の説明ページを追加する。

**Architecture:** 静的テンプレート1枚＋薄いルート。既存スタイル再利用。

**設計書:** `docs/superpowers/specs/2026-07-02-about-page-design.md`

## Global Constraints

- 指標名はUI表記と完全一致。既存142テスト無修正で全pass
- ルートのDBアクセスは `last_synced` のみ

---

### Task 1: /aboutページ

**Files:**
- Modify: `web/app.py`（ルート追加）
- Create: `web/templates/about.html`
- Modify: `web/templates/base.html`（navリンク＋フッター見出しのリンク化）
- Test: `tests/test_web.py`

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_web.py`）

```python
def test_about_page(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    body = resp.text
    for heading in ("このサイトについて", "データソースと更新", "研究者の統合と名寄せ",
                    "指標の定義", "学内順位の読み方", "限界"):
        assert heading in body
    assert "1.0が世界平均" in body
    assert "著者数で割った" in body
    assert "OpenAlex収録分に基づく" in body


def test_about_nav_link(client):
    body = client.get("/").text
    assert 'href="/about"' in body
```

- [ ] **Step 2: 失敗を確認** → FAIL

- [ ] **Step 3: ルート追加**（`web/app.py`、`departments_page` の後）

```python
    @app.get("/about", response_class=HTMLResponse)
    def about_page(request: Request):
        with Session(engine) as session:
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "about.html",
                                          {"synced": synced})
```

- [ ] **Step 4: base.html**

- navの「部局」の後に `<a href="/about" {% if path == '/about' %}class="current"{% endif %}>指標の説明</a>`（既存の current 付与パターンに合わせる）
- フッター `<p class="foot-title">データについて</p>` を `<p class="foot-title"><a href="/about">データについて</a></p>` に

- [ ] **Step 5: about.html作成**（以下の内容そのまま。`{{ ... }}` はJinja）

```html
{% extends "base.html" %}
{% block title %}指標の説明 - OMU at a glance{% endblock %}
{% block content %}
<h2>指標の説明</h2>

<section class="metric-section">
<h3>このサイトについて</h3>
<p>OMU at a glance は、大阪公立大学に所属する研究者の研究活動を、公開データに基づいて多面的に比較するための内部ツールです。すべての集計は「収録分に基づく」値であり、研究者の全業績を表すものではありません。単一の指標で研究者を評価せず、複数の指標と学内順位をあわせて解釈してください。</p>
</section>

<section class="metric-section">
<h3>データソースと更新</h3>
<table>
<thead><tr><th>ソース</th><th>取得内容</th><th>範囲</th></tr></thead>
<tbody>
<tr><td><a href="https://openalex.org/" target="_blank">OpenAlex</a>（CC0）</td><td>論文・被引用数・FWCI・著者・所属</td><td>直近3年のローリング窓（実行日から3年前まで）。OpenAlex収録分に基づく</td></tr>
<tr><td><a href="https://kaken.nii.ac.jp/" target="_blank">KAKEN</a></td><td>科研費課題（種目・期間・配分額・代表/分担）</td><td>直近3年と期間が重なる課題。大阪公立大学名義のみ（前身大学名義は含まない）</td></tr>
<tr><td>大学公式研究者総覧</td><td>部局・職位・日本語氏名・受賞・著書・講演・委員歴</td><td>実績は全期間・本人（大学）登録内容</td></tr>
</tbody>
</table>
<p>全データは週次で全量更新されます。被引用数・FWCIは更新のたびに最新値へ洗い替えられます。</p>
</section>

<section class="metric-section">
<h3>研究者の統合と名寄せ</h3>
<p>OpenAlexでは同一人物に複数の著者レコードが作られることがあります。本サイトは、同一ORCID・同一論文の共有・共著者の重なりといった証拠がある場合のみレコードを統合しています（名前の一致だけでは統合しません）。</p>
<p>日本語氏名・部局・科研費は、KAKENと公式総覧の氏名（漢字・カナ）をローマ字照合して紐付けています。誤った人物への紐付けを避けるため、学内で一意に特定できる場合のみ自動確定しており、一部の研究者は名寄せできていません。名寄せできていない研究者の部局・科研費・実績は「–」と表示されます。</p>
</section>

<section class="metric-section">
<h3>指標の定義</h3>

<h4>インパクト</h4>
<table>
<thead><tr><th>指標</th><th>定義</th></tr></thead>
<tbody>
<tr><td>FWCI</td><td>Field-Weighted Citation Impact。論文の被引用数を、同じ分野・同じ出版年・同じ文献タイプの世界平均で正規化した値で、<strong>1.0が世界平均</strong>です。分野による被引用数の桁違い（医学は多く数学は少ない等）を補正して比較できます。</td></tr>
<tr><td>FWCI合計</td><td>直近3年の論文のFWCIの合計。量×質を反映し、<strong>既定の並び順</strong>です。FWCI平均は論文数が少ない研究者ほど1本の高FWCI論文（偶然の共著等）に支配されるため、継続的な研究活動を見るには合計が適しています。</td></tr>
<tr><td>FWCI平均 / FWCI中央値</td><td>直近3年の論文のFWCIの平均と中央値。少数の当たり論文の影響を見るには平均と中央値の乖離が手がかりになります。</td></tr>
<tr><td>総被引用数</td><td>直近3年の論文が受けた被引用数の合計（分野補正なしの生値）。</td></tr>
<tr><td>被引用(補正)</td><td>各論文の被引用数を<strong>著者数で割った</strong>値の合計。数百人規模の大規模共著論文で被引用数が膨らむのを補正します。著者数が取得上限で打ち切られた論文は計算から除外します。</td></tr>
<tr><td>top10% / top1%論文数</td><td>分野・出版年内で被引用数が上位10%（1%）に入る論文の数。</td></tr>
</tbody>
</table>

<h4>生産性</h4>
<table>
<thead><tr><th>指標</th><th>定義</th></tr></thead>
<tbody>
<tr><td>3年論文数</td><td>直近3年のOpenAlex収録論文数（preprint等も含む）。</td></tr>
<tr><td>論文数(補正)</td><td>各論文を1/著者数として数えた合計（fractional counting）。</td></tr>
<tr><td>筆頭著者数 / 責任著者数</td><td>直近3年のうち筆頭（first）・責任（corresponding）として関与した論文数。</td></tr>
</tbody>
</table>

<h4>連携・資金</h4>
<table>
<thead><tr><th>指標</th><th>定義</th></tr></thead>
<tbody>
<tr><td>国際共著率</td><td>著者の所属国に日本以外が含まれる論文の割合。<strong>著者の所属国ベース</strong>のため、二重所属の著者がいる論文も国際共著に数えます。</td></tr>
<tr><td>産学連携率</td><td>企業所属の著者が含まれる論文の割合。</td></tr>
<tr><td>OA率</td><td>オープンアクセスで読める論文の割合。</td></tr>
<tr><td>科研費（代表 / 分担）</td><td>直近3年と期間が重なる科研費課題のうち、研究代表者・研究分担者として関与している件数。</td></tr>
<tr><td>科研費配分総額</td><td><strong>研究代表者を務める課題のみ</strong>の配分総額（直接＋間接）の合計。課題の総額であり、按分はしていません。分担課題の金額は本人の獲得額とは言えないため合算しません。</td></tr>
</tbody>
</table>

<h4>研究者指標・実績（全期間）</h4>
<table>
<thead><tr><th>指標</th><th>定義</th></tr></thead>
<tbody>
<tr><td>h指数 / i10指数</td><td>OpenAlexによる全期間の値。h指数はh回以上引用された論文がh本あることを示します。i10は10回以上引用された論文数です。</td></tr>
<tr><td>2年平均被引用</td><td>直近2年の論文1本あたり平均被引用数（OpenAlex）。</td></tr>
<tr><td>受賞 / 著書 / 講演 / 委員歴</td><td>公式研究者総覧の登録件数（全期間）。<strong>本人（大学）登録の内容のため、網羅性は研究者により異なります</strong>。分野の慣習（講演の多い分野・少ない分野）にも留意してください。</td></tr>
<tr><td>主分野</td><td>直近3年の論文で最も多い研究サブ分野（OpenAlexの分類）。</td></tr>
</tbody>
</table>
</section>

<section class="metric-section">
<h3>学内順位の読み方</h3>
<ul>
<li>母数は「直近3年に論文1本以上」の研究者です。</li>
<li>同じ値の研究者は同じ順位になります（上位詰め）。</li>
<li>値が0の指標には順位を表示しません（0は大多数と同率であり、順位に意味がないため）。</li>
</ul>
</section>

<section class="metric-section">
<h3>限界</h3>
<ul>
<li>OpenAlexに収録されていない業績（和文誌・書籍中心の分野など）は反映されません。人文・社会系は特に過小評価になりやすい点に注意してください。</li>
<li>preprintと出版版が別論文として重複計上される場合があります。</li>
<li>ごく大規模な共著論文では著者リストが取得上限で打ち切られ、著者数を使う補正の対象外になります。</li>
<li>名寄せできていない研究者には部局・科研費・実績が表示されません。同一人物のレコード統合も証拠がある場合に限るため、一部の研究者は業績が分かれたままの可能性があります。</li>
<li>受賞・著書・講演・委員歴は本人登録に依存し、登録の熱心さの差がそのまま件数の差になります。</li>
</ul>
</section>
{% endblock %}
```

（`h4` と定義テーブル用に style.css へ追記: ）

```css
.metric-section h4 { margin: 0.9rem 0 0.35rem; font-size: 0.88rem; color: var(--indigo-deep); }
.metric-section table { margin-bottom: 0.4rem; }
.metric-section td:first-child { white-space: nowrap; font-weight: 600; width: 12rem; }
```

（↑最後のルールはaboutの定義表以外に影響し得るため、`about.html` のtableに `class="defs"` を付け `table.defs td:first-child {...}` とスコープすること。h4ルールは共通で害なし）

- [ ] **Step 6: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全pass（既存無修正）

- [ ] **Step 7: Commit**

```bash
git add web tests/test_web.py
git commit -m "feat: 指標説明ページ（/about）"
```

---

## 完了条件

- `/about` が表示され、navとフッターから到達できる。既存テスト無修正で全pass
