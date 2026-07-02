# 公式総覧の個人ページ実績 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公式総覧の個人ページから受賞・著書・講演・委員歴を収集し、比較指標4種と受賞歴リスト表示を追加する。

**Architecture:** `collector/roster.py` に個人ページパーサ＋`sync_profiles` を追記（既存RosterClient再利用）。`roster_achievements` テーブル→ compute_metrics で名寄せ経由の件数集計→詳細カード・受賞リスト・比較グループ。

**Tech Stack:** 既存のみ（bs4は導入済み）。

**設計書:** `docs/superpowers/specs/2026-07-02-roster-achievements-design.md`

## Global Constraints

- 個人ページ: `/html/{profile_id}_ja.html`。セクション: `div#jusho`→award / `div#chosho`→book / `div#knkyu_prsn`→presentation / `div#gkkai_iinkai`→committee
- エントリ: コンテナ内 `li` の `p.title`（無ければスキップ）。detail = liの `p.contents` のうち **`.gaiyo-detail`（詳細アコーディオン）内でない**最初の非空テキスト。year = detailから最初の `(19|20)\d{2}` をint化（無ければNone）
- 全洗い替え。取得成功ページがroster人数の**50%未満なら洗い替えスキップ＋警告**。ページ単位の失敗はスキップ＋警告で続行
- metrics 4列は**全期間値**。名寄せ済みrosterの実績のみ、matched_researcher_id は canon() を通す
- 表示ラベルは「受賞（全期間）」等、比較グループ名は「実績（全期間・公式総覧）」
- フッター注記追記: 「受賞・著書・講演・委員歴は公式総覧収録分（全期間・名寄せ済み研究者のみ）。」

---

### Task 1: モデル＋個人ページパーサ

**Files:**
- Modify: `db/models.py`（RosterAchievement追加、ResearcherMetricsに4列）
- Modify: `collector/roster.py`（parse_profile_achievements追記）
- Test: `tests/test_profile_parse.py`

**Interfaces:**
- Produces:
  - `db.models.RosterAchievement(id: int [PK autoincrement], profile_id: str [index], category: str, title: str, year: int|None, detail: str|None, updated_at: str)`
  - `ResearcherMetrics` 追加列: `awards_count` / `books_count` / `presentations_count` / `committee_count`（int, default 0）
  - `collector.roster.parse_profile_achievements(html: str) -> list[dict]` — dict: category/title/year/detail（profile_idはsync側で付与）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_profile_parse.py`。fixtureは実ページ構造の縮小版）

```python
from collector.roster import parse_profile_achievements

PROFILE_HTML = """<html><body>
<div id="jusho">
  <ul>
    <li>
      <p class="title">第11回日本歴史学会賞</p>
      <p class="contents gray"></p>
      <p class="contents">2010 日本歴史学会 </p>
      <div class="accordion-menu">
        <div class="is-none-disp-gaiyo gaiyo-detail">
          <p class="contents"><span>受賞国：</span>日本国</p>
        </div>
      </div>
    </li>
    <li>
      <p class="title">2023年度学長表彰（研究分野）</p>
      <p class="contents">2023年12月 大阪公立大学 </p>
    </li>
    <li><p class="contents">タイトル無し行（スキップ対象）</p></li>
  </ul>
</div>
<div id="chosho">
  <ul>
    <li>
      <p class="title">日本古代の郡司と天皇</p>
      <p class="contents">吉川弘文館 2016年11月 </p>
    </li>
  </ul>
</div>
<div id="knkyu_prsn">
  <ul>
    <li>
      <p class="title">御毛寺知識経と地域社会</p>
      <p class="contents">続日本紀研究会例会 2024年06月</p>
    </li>
  </ul>
</div>
<div id="gkkai_iinkai">
  <ul>
    <li>
      <p class="title">和泉市史編さん委員会 調査執筆委員</p>
      <p class="contents">年不明</p>
    </li>
  </ul>
</div>
</body></html>"""


def test_parse_profile_achievements():
    entries = parse_profile_achievements(PROFILE_HTML)
    by_cat = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
    awards = by_cat["award"]
    assert len(awards) == 2                       # タイトル無しliはスキップ
    assert awards[0]["title"] == "第11回日本歴史学会賞"
    assert awards[0]["year"] == 2010
    assert "日本歴史学会" in awards[0]["detail"]
    assert "受賞国" not in awards[0]["detail"]     # アコーディオン内は使わない
    assert awards[1]["year"] == 2023
    assert by_cat["book"][0]["year"] == 2016
    assert by_cat["presentation"][0]["year"] == 2024
    assert by_cat["committee"][0]["year"] is None  # 年が無ければNone
    assert len(entries) == 5


def test_parse_profile_achievements_missing_sections():
    assert parse_profile_achievements("<html><body>なにもない</body></html>") == []
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_profile_parse.py -v` → FAIL（import error）

- [ ] **Step 3: モデル追加**（`db/models.py`）

`Roster` クラスの後に:

```python
class RosterAchievement(Base):
    __tablename__ = "roster_achievements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True,
                                    autoincrement=True)
    profile_id: Mapped[str] = mapped_column(String, index=True)
    category: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[str] = mapped_column(String)
```

`ResearcherMetrics` の `kaken_total_amount` 行の直後に:

```python
    awards_count: Mapped[int] = mapped_column(Integer, default=0)
    books_count: Mapped[int] = mapped_column(Integer, default=0)
    presentations_count: Mapped[int] = mapped_column(Integer, default=0)
    committee_count: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: パーサ実装**（`collector/roster.py` に追記）

```python
ACHIEVEMENT_SECTIONS = {
    "jusho": "award",
    "chosho": "book",
    "knkyu_prsn": "presentation",
    "gkkai_iinkai": "committee",
}
YEAR_RE = re.compile(r"(19|20)\d{2}")


def parse_profile_achievements(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    for section_id, category in ACHIEVEMENT_SECTIONS.items():
        container = soup.find("div", id=section_id)
        if container is None:
            continue
        for li in container.find_all("li"):
            title_el = li.find("p", class_="title")
            if title_el is None:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue
            detail = None
            for p in li.find_all("p", class_="contents"):
                if p.find_parent(class_="gaiyo-detail") is not None:
                    continue
                text = p.get_text(" ", strip=True)
                if text:
                    detail = text
                    break
            year_m = YEAR_RE.search(detail or "")
            entries.append({
                "category": category,
                "title": title,
                "year": int(year_m.group(0)) if year_m else None,
                "detail": detail,
            })
    return entries
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_profile_parse.py -v` → PASS (2 tests)。全suite PASS

- [ ] **Step 6: Commit**

```bash
git add db/models.py collector/roster.py tests/test_profile_parse.py
git commit -m "feat: 個人ページ実績パーサとroster_achievementsテーブル"
```

---

### Task 2: sync_profiles＋メトリクス集計＋CLI

**Files:**
- Modify: `collector/roster.py`（sync_profiles追記）
- Modify: `collector/metrics.py`（4指標の集計）
- Modify: `scripts/sync.py`（rosterブロック内にprofilesステージ）
- Test: `tests/test_roster_sync.py`, `tests/test_metrics.py`

**Interfaces:**
- Produces:
  - `collector.roster.sync_profiles(session, client, today) -> int` — roster全員の個人ページを取得→実績を全洗い替え（成功<50%でスキップ）→保存エントリ数を返す。`sync_state`(source="profiles")
  - compute_metrics が awards/books/presentations/committee の件数を集計（名寄せ済みrosterのみ、matched idはcanon()経由）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_roster_sync.py` に追加（importに `sync_profiles`, `RosterAchievement` を追加）:

```python
PROFILE_A = """<div id="jusho"><ul><li><p class="title">賞X</p>
<p class="contents">2020 機関</p></li></ul></div>"""


class FakeProfileClient:
    def __init__(self, pages: dict, fail: set = frozenset()):
        self.pages = pages
        self.fail = fail
        self.calls = []

    def fetch(self, path, params=None):
        self.calls.append(path)
        pid = path.split("/")[-1].replace("_ja.html", "")
        if pid in self.fail:
            raise RuntimeError("fetch failed")
        return self.pages.get(pid, "<html></html>")


def test_sync_profiles_stores_achievements():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([
            Roster(profile_id="111", name_kanji="山田 太郎", division="D",
                   updated_at=""),
            Roster(profile_id="222", name_kanji="鈴木 花子", division="D",
                   updated_at=""),
        ])
        s.commit()
        client = FakeProfileClient({"111": PROFILE_A})
        n = sync_profiles(s, client, today=TODAY)
        assert n == 1
        rows = list(s.scalars(select(RosterAchievement)))
        assert rows[0].profile_id == "111"
        assert rows[0].category == "award"
        assert rows[0].year == 2020
        assert s.get(SyncState, "profiles").last_synced_at == "2026-07-02"
        assert "/html/111_ja.html" in client.calls


def test_sync_profiles_majority_failure_keeps_existing(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([
            Roster(profile_id="111", name_kanji="a", division="D",
                   updated_at=""),
            Roster(profile_id="222", name_kanji="b", division="D",
                   updated_at=""),
            Roster(profile_id="333", name_kanji="c", division="D",
                   updated_at=""),
        ])
        s.add(RosterAchievement(profile_id="OLD", category="award",
                                title="既存の賞", year=2000, detail=None,
                                updated_at=""))
        s.commit()
        client = FakeProfileClient({}, fail={"111", "222"})  # 2/3失敗
        with caplog.at_level("WARNING"):
            assert sync_profiles(s, client, today=TODAY) == 0
        assert s.scalars(select(RosterAchievement)).first().title == "既存の賞"
```

`tests/test_metrics.py` の `test_compute_metrics` にseed追加（kakenのseedの後、`s.commit()` 前。importに `Roster, RosterAchievement` 追加）:

```python
        s.add(Roster(profile_id="P1", name_kanji="x", division="D",
                     matched_researcher_id="A1", updated_at=""))
        s.add_all([
            RosterAchievement(profile_id="P1", category="award", title="賞1",
                              year=2020, detail=None, updated_at=""),
            RosterAchievement(profile_id="P1", category="award", title="賞2",
                              year=2021, detail=None, updated_at=""),
            RosterAchievement(profile_id="P1", category="book", title="本1",
                              year=2019, detail=None, updated_at=""),
            RosterAchievement(profile_id="P9", category="award", title="無関係",
                              year=2020, detail=None, updated_at=""),
        ])
```

アサーション追加（m1ブロック）:

```python
        assert m1.awards_count == 2
        assert m1.books_count == 1
        assert m1.presentations_count == 0
```

（m2ブロック）:

```python
        assert m2.awards_count == 0
```

- [ ] **Step 2: 失敗を確認** → FAIL

- [ ] **Step 3: sync_profiles実装**（`collector/roster.py` に追記。importに `RosterAchievement` 追加）

```python
def sync_profiles(session, client, today: datetime.date) -> int:
    profile_ids = list(session.scalars(select(Roster.profile_id)))
    if not profile_ids:
        logger.warning("profiles: rosterが空のためスキップ")
        return 0
    rows: list[dict] = []
    ok = 0
    for pid in profile_ids:
        try:
            html = client.fetch(f"/html/{pid}_ja.html")
        except Exception as e:
            logger.warning("profileページ取得失敗 %s: %s", pid, e)
            continue
        ok += 1
        for entry in parse_profile_achievements(html):
            rows.append({**entry, "profile_id": pid})
    if ok < len(profile_ids) * 0.5:
        logger.warning(
            "profiles: 取得成功が5割未満（%d/%d）のため洗い替えをスキップ",
            ok, len(profile_ids))
        return 0
    session.execute(delete(RosterAchievement))
    for row in rows:
        session.add(RosterAchievement(**row, updated_at=today.isoformat()))
    session.merge(SyncState(source="profiles", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("profiles: %d人分 %d件の実績", ok, len(rows))
    return len(rows)
```

- [ ] **Step 4: metrics集計**（`collector/metrics.py`。importに `Roster, RosterAchievement` 追加）

`kaken_by_author` 構築の直後に:

```python
    ach_by_author: dict[str, dict] = {}
    for rid, category in session.execute(
        select(Roster.matched_researcher_id, RosterAchievement.category)
        .join(RosterAchievement,
              RosterAchievement.profile_id == Roster.profile_id)
        .where(Roster.matched_researcher_id.is_not(None))
    ):
        counts = ach_by_author.setdefault(canon(rid), {})
        counts[category] = counts.get(category, 0) + 1
```

`ResearcherMetrics(...)` の `kaken_total_amount=...` の直後に:

```python
            awards_count=ach_by_author.get(rid, {}).get("award", 0),
            books_count=ach_by_author.get(rid, {}).get("book", 0),
            presentations_count=ach_by_author.get(rid, {}).get(
                "presentation", 0),
            committee_count=ach_by_author.get(rid, {}).get("committee", 0),
```

- [ ] **Step 5: CLI**（`scripts/sync.py` rosterブロック内、`match_roster` の後に）

```python
            n_p = sync_profiles(session, RosterClient(), today=today)
            logger.info("profiles: %d件", n_p)
```

（importの `sync_roster` 行に `sync_profiles` を追加。rosterブロックのtry/except内なので失敗時も他ステージ継続）

- [ ] **Step 6: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 7: Commit**

```bash
git add collector/roster.py collector/metrics.py scripts/sync.py tests/test_roster_sync.py tests/test_metrics.py
git commit -m "feat: 個人ページ実績の同期と実績4指標の集計"
```

---

### Task 3: Web表示

**Files:**
- Modify: `web/queries.py`（awards_for追加）
- Modify: `web/app.py`（awards context・比較4行）
- Modify: `web/templates/researcher.html`, `base.html`
- Modify: `tests/conftest.py`
- Test: `tests/test_web.py`, `tests/test_web_queries.py`

**Interfaces:**
- Produces: `queries.awards_for(session, researcher_id) -> list[RosterAchievement]`（award、year降順・NULL末尾）。詳細カード4項目＋受賞歴リスト、比較「実績（全期間・公式総覧）」グループ

- [ ] **Step 1: conftest拡張**

metricsのA1に `awards_count=3, books_count=2, presentations_count=10, committee_count=1,`（`kaken_total_amount` の後）、A2に `awards_count=0, books_count=0, presentations_count=0, committee_count=0,`、A3はdefault。
Rosterのseed追加（既存Researcher群の後）:

```python
        s.add(Roster(profile_id="P1", name_kanji="山田 太郎", division="大学院医学研究科",
                     matched_researcher_id="A1", updated_at=""))
        s.add_all([
            RosterAchievement(profile_id="P1", category="award",
                              title="ベスト研究賞", year=2024, detail="2024 学会",
                              updated_at=""),
            RosterAchievement(profile_id="P1", category="award",
                              title="古い賞", year=None, detail=None,
                              updated_at=""),
        ])
```

（importに `Roster, RosterAchievement` を追加）

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_web_queries.py`:

```python
def test_awards_for(seeded_db_path):
    with _session(seeded_db_path) as s:
        awards = awards_for(s, "A1")
        assert [a.title for a in awards] == ["ベスト研究賞", "古い賞"]  # NULL年は末尾
        assert awards_for(s, "A2") == []
```

（importに `awards_for` 追加）

`tests/test_web.py`:

```python
def test_researcher_detail_achievements(client):
    body = client.get("/researchers/A1").text
    assert "受賞（全期間）" in body and "著書（全期間）" in body
    assert "ベスト研究賞" in body   # 受賞歴リスト
    body2 = client.get("/researchers/A2").text
    assert "ベスト研究賞" not in body2


def test_compare_achievements_group(client):
    body = client.get("/compare?ids=A1,A2").text
    assert "実績（全期間・公式総覧）" in body
    assert '<td class="best">3</td>' in body   # 受賞数A1=3
    assert "受賞・著書・講演・委員歴は公式総覧収録分" in body
```

- [ ] **Step 3: 失敗を確認** → FAIL

- [ ] **Step 4: 実装**

`web/queries.py` 末尾（importに `RosterAchievement, Roster` 追加）:

```python
def awards_for(session, researcher_id):
    return list(session.scalars(
        select(RosterAchievement)
        .join(Roster, Roster.profile_id == RosterAchievement.profile_id)
        .where(Roster.matched_researcher_id == researcher_id,
               RosterAchievement.category == "award")
        .order_by(RosterAchievement.year.desc())
    ))
```

（SQLiteのDESCはNULLを末尾に置く）

`web/app.py` `researcher_page`: `ranks = ...` の直後に `awards = queries.awards_for(session, openalex_id)`、contextに `"awards": awards,` を追加。

`_compare_table` の連携・資金グループの後に新グループ:

```python
        ("実績（全期間・公式総覧）", [
            ("受賞数", metric("awards_count"), _fmt_int, True),
            ("著書数", metric("books_count"), _fmt_int, True),
            ("講演数", metric("presentations_count"), _fmt_int, True),
            ("委員歴数", metric("committee_count"), _fmt_int, True),
        ]),
```

`web/templates/researcher.html`:
- メトリクスカード（`{% if m %}` 内、科研費配分総額の後）に:

```html
  <div><dt>受賞（全期間）</dt><dd>{{ m.awards_count }}</dd></div>
  <div><dt>著書（全期間）</dt><dd>{{ m.books_count }}</dd></div>
  <div><dt>講演（全期間）</dt><dd>{{ m.presentations_count }}</dd></div>
  <div><dt>委員歴（全期間）</dt><dd>{{ m.committee_count }}</dd></div>
```

- 論文リストの `<h3>` の前に受賞リスト:

```html
{% if awards %}
<h3>受賞歴（公式総覧・全期間）</h3>
<ul class="awards">
{% for a in awards %}
  <li>{{ a.title }}{% if a.year %}<span class="year">（{{ a.year }}）</span>{% endif %}</li>
{% endfor %}
</ul>
{% endif %}
```

`web/templates/base.html` フッター注記の末尾に: `受賞・著書・講演・委員歴は公式総覧収録分（全期間・名寄せ済み研究者のみ）。`

`web/static/style.css`:

```css
ul.awards { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 0.8rem 2rem; }
ul.awards .year { color: var(--muted); font-size: 0.85rem; }
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine（`test_researcher_without_metrics_renders` 等の既存テストが「受賞」文字列に影響されないか確認）

- [ ] **Step 6: Commit**

```bash
git add web tests
git commit -m "feat: 実績カード・受賞歴リスト・比較の実績グループ"
```

---

### Task 4: DB再構築＋実クロール＋検証

**Files:** 実行のみ

- [ ] **Step 1: 再構築＋full sync（合計25〜35分の見込み）**

スキーマ変更（roster_achievements＋metrics4列）のため再構築。**同期は10分を超えるため、Bashの `run_in_background: true` で起動し、完了通知を待ってからログを確認する**:

```bash
rm -f db/researchers.db db/researchers.db-wal db/researchers.db-shm
# run_in_background で:
uv run python scripts/sync.py > /tmp/claude-3001/-srv-apps-researchers/b93b6076-a79b-4b9e-81c1-82625ec942a9/scratchpad/sync_ach.log 2>&1
```

完了後: `tail -5 <log>` で `profiles: <1300前後>人分 <数万>件の実績` と `done: ...` を確認、警告なし

- [ ] **Step 2: スポットチェック**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
q = lambda sql: c.execute(sql).fetchall()
print('achievements:', q('SELECT category, COUNT(*) FROM roster_achievements GROUP BY category'))
print('awards>0:', q('SELECT COUNT(*) FROM researcher_metrics WHERE awards_count > 0'))
print('top awards:', q('SELECT r.name_ja, m.awards_count, m.books_count FROM researcher_metrics m JOIN researchers r ON r.openalex_id=m.researcher_id ORDER BY m.awards_count DESC LIMIT 5'))
"
```

Expected: 4カテゴリに実データ（presentationが最多・数万件規模、awardが数千件規模）、awards>0が数百人、上位に妥当な氏名と件数

- [ ] **Step 3: 実表示確認（port 8199）**

受賞数上位の研究者IDで詳細ページに「受賞歴（公式総覧・全期間）」リストが出ること、`/compare` に実績グループが出ることをcurlで確認。kill＋死活確認。

- [ ] **Step 4: git確認**

```bash
git status --short   # 空
```

---

## 完了条件

- 全テストPASS。実DBに実績が入り、詳細ページの受賞リスト・比較の実績グループが実データで表示される
