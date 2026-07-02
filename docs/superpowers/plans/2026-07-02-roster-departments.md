# 部局統合（Phase 2） 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公式総覧から部局・職位・公式日本語氏名を取得して名寄せし、ランキングの部局フィルタと部局間比較ページを追加する。

**Architecture:** `collector/roster.py`（クライアント＋bs4パーサ＋sync＋二重ブリッジ名寄せ）、`collector/kaken.py` に公式総覧優先ガード、web層に department フィルタ＋ `/departments`。スキーマは新テーブル追加のみ（create_allが自動作成、DB再構築不要）。

**Tech Stack:** 既存＋ `beautifulsoup4==4.12.3`（パーサは stdlib html.parser を使用、lxml は追加しない）。

**設計書:** `docs/superpowers/specs/2026-07-02-roster-departments-design.md`

## Global Constraints

- クロール: 全リクエストに0.5秒スリープ、リトライは既存パターン（1,2,4,8,16秒・最大6回、429/5xx＋TransportError）。取得0件は洗い替えスキップ＋警告（全消しガード）
- 一覧URL: `/search?m=affiliation&l=ja&a2=<code>&s=<start>&o=affiliation&pp=100`。総件数は「◯件中」から。部局コードとラベルはトップページの a2リンク（a3付きリンクは除外）から
- 名寄せ: 漢字ブリッジ（roster.name_kanji == researchers.name_ja）＋カナブリッジ（既存nameutil）の候補和集合が**ちょうど1人**の場合のみ確定。複数roster行が同一研究者を主張したら**全て未マッチ**
- 反映: 確定者に department/position/name_ja/is_official_roster=True。今回未マッチになった旧roster研究者は department/position/is_official_roster をクリア、**name_jaは残す**（KAKEN名寄せが管理を引き継ぐ）
- KAKENガード: `match_members` は `is_official_roster=True` の研究者の name_ja を設定も消去もしない
- 氏名の全角空白は半角に正規化
- webの部局名パラメタは実在部局の完全一致のみ有効（不正値は全学表示）。ソートリンク・ページャは department を保持

---

### Task 1: 依存＋Rosterモデル＋クローラ/パーサ

**Files:**
- Modify: `pyproject.toml`（dependencies に `"beautifulsoup4==4.12.3"` 追加、`uv sync`）
- Modify: `db/models.py`（Rosterテーブル）
- Create: `collector/roster.py`（クライアント＋パーサ。sync/名寄せはTask 2で追記）
- Test: `tests/test_roster_parse.py`

**Interfaces:**
- Produces:
  - `db.models.Roster(profile_id: str [PK], name_kanji: str, name_kana: str|None, position: str|None, division: str, org_text: str|None, keywords: str|None, matched_researcher_id: str|None [index], updated_at: str)`
  - `collector.roster.RosterClient(transport=None, sleep_fn=time.sleep)` — `.fetch(path, params=None) -> str`（成功後に0.5秒スリープ）
  - `collector.roster.parse_divisions(html) -> list[tuple[str, str]]` — (a2コード, 部局ラベル)。a3付き・重複コードは除外
  - `collector.roster.parse_list_page(html) -> tuple[list[dict], int]` — member dict: `profile_id/name_kanji/name_kana/position/org_text/keywords`（divisionはsync側で付与）。壊れカードはスキップ＋警告

- [ ] **Step 1: 依存追加**

`pyproject.toml` dependencies に `"beautifulsoup4==4.12.3",` を追加し `uv sync`。

- [ ] **Step 2: 失敗するテストを書く**（`tests/test_roster_parse.py`。fixtureは実サイトの実構造を縮小したもの）

```python
import pytest

from collector.roster import parse_divisions, parse_list_page

HOME_HTML = """<html><body>
<a href="/search?m=affiliation&amp;l=ja&amp;a2=0000001&amp;s=1&amp;o=affiliation">大学院文学研究科</a>
<a href="/search?m=affiliation&amp;l=ja&amp;a2=0000001&amp;a3=0000053&amp;s=1&amp;o=affiliation">哲学歴史学専攻</a>
<a href="/search?m=affiliation&amp;l=ja&amp;a2=0000002&amp;s=1&amp;o=affiliation">大学院医学研究科</a>
<a href="/search?m=affiliation&amp;l=ja&amp;a2=0000002&amp;s=1&amp;o=affiliation">大学院医学研究科(重複)</a>
<a href="/other">無関係</a>
</body></html>"""

LIST_HTML = """<html><body>
<div>検索結果</div><div>64件中 1〜2件を表示</div>
<div class="card-body result">
  <div class="psn-name">
    <div class="name-kna">イワシタ　トオル</div>
    <div class="name-gng"><a href="/html/100000729_ja.html">磐下　徹</a></div>
    <div class="name-title">教授</div>
  </div>
  <p class="h6 org-cd">大学院文学研究科 哲学歴史学専攻<br/>文学部 哲学歴史学科</p>
  <p class="kaknh-bnrui">古代史、郡司</p>
</div>
<div class="card-body result">
  <div class="psn-name">
    <div class="name-gng"><a href="/html/100000560_ja.html">上野　雅由樹</a></div>
  </div>
</div>
<div class="card-body result">
  <div class="psn-name"><div class="name-kna">リンクなし</div></div>
</div>
</body></html>"""


def test_parse_divisions():
    divisions = parse_divisions(HOME_HTML)
    assert divisions == [("0000001", "大学院文学研究科"),
                         ("0000002", "大学院医学研究科")]


def test_parse_list_page(caplog):
    with caplog.at_level("WARNING"):
        members, total = parse_list_page(LIST_HTML)
    assert total == 64
    assert len(members) == 2
    m1 = members[0]
    assert m1["profile_id"] == "100000729"
    assert m1["name_kanji"] == "磐下 徹"       # 全角空白→半角
    assert m1["name_kana"] == "イワシタ　トオル"
    assert m1["position"] == "教授"
    assert "大学院文学研究科 哲学歴史学専攻" in m1["org_text"]
    assert m1["keywords"] == "古代史、郡司"
    m2 = members[1]
    assert m2["profile_id"] == "100000560"
    assert m2["name_kana"] is None and m2["position"] is None
    # 3枚目（氏名リンクなし）はスキップ＋警告
    assert any("スキップ" in r.message for r in caplog.records)


def test_parse_list_page_empty():
    members, total = parse_list_page("<html><body>0件中</body></html>")
    assert members == [] and total == 0
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_roster_parse.py -v` → FAIL（import error）

- [ ] **Step 4: モデル追加**（`db/models.py`、`GrantMember` クラスの後）

```python
class Roster(Base):
    __tablename__ = "roster"
    profile_id: Mapped[str] = mapped_column(String, primary_key=True)
    name_kanji: Mapped[str] = mapped_column(String)
    name_kana: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    division: Mapped[str] = mapped_column(String)
    org_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_researcher_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True)
    updated_at: Mapped[str] = mapped_column(String)
```

- [ ] **Step 5: クライアント＋パーサ実装**（`collector/roster.py` 新規）

```python
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://kyoiku-kenkyudb.omu.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6
PAGE_SIZE = 100
CRAWL_DELAY_SECONDS = 0.5  # 公式総覧への礼儀としてのアクセス間隔

DIVISION_HREF = re.compile(
    r"^/search\?m=affiliation&l=ja&a2=(\d+)&s=1&o=affiliation$")
TOTAL_RE = re.compile(r"([0-9,]+)\s*件中")
PROFILE_RE = re.compile(r"/html/(\d+)_ja\.html")


class RosterClient:
    def __init__(self, transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport, follow_redirects=True)

    def fetch(self, path: str, params: dict | None = None) -> str:
        for attempt in range(MAX_TRIES):
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt == MAX_TRIES - 1:
                    raise
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_TRIES - 1:
                    break
                wait = 2 ** attempt
                logger.warning("roster -> %s, retry in %ss",
                               resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            self._sleep(CRAWL_DELAY_SECONDS)
            return resp.text
        resp.raise_for_status()
        raise RuntimeError("unreachable")


def parse_divisions(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    divisions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = DIVISION_HREF.match(a["href"])
        if not m:
            continue
        code = m.group(1)
        label = a.get_text(strip=True)
        if code in seen or not label:
            continue
        seen.add(code)
        divisions.append((code, label))
    return divisions


def parse_list_page(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    total_m = TOTAL_RE.search(soup.get_text())
    total = int(total_m.group(1).replace(",", "")) if total_m else 0
    members: list[dict] = []
    for card in soup.select("div.card-body.result"):
        link = card.select_one(".name-gng a[href]")
        if link is None:
            logger.warning("rosterカードをスキップ: 氏名リンクなし")
            continue
        pm = PROFILE_RE.search(link["href"])
        if pm is None:
            logger.warning("rosterカードをスキップ: profile ID不明 (%s)",
                           link["href"])
            continue
        kana_el = card.select_one(".name-kna")
        title_el = card.select_one(".name-title")
        org_el = card.select_one(".org-cd")
        kw_el = card.select_one(".kaknh-bnrui")
        kana = kana_el.get_text(strip=True) if kana_el else None
        position = title_el.get_text(strip=True) if title_el else None
        members.append({
            "profile_id": pm.group(1),
            "name_kanji": link.get_text(strip=True).replace("　", " "),
            "name_kana": kana or None,
            "position": position or None,
            "org_text": org_el.get_text(" ", strip=True) if org_el else None,
            "keywords": kw_el.get_text(strip=True) if kw_el else None,
        })
    return members, total
```

- [ ] **Step 6: テスト通過を確認**

Run: `uv run pytest tests/test_roster_parse.py -v` → PASS (3 tests)。全suite PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock db/models.py collector/roster.py tests/test_roster_parse.py
git commit -m "feat: 公式総覧のクローラとパーサ（rosterテーブル）"
```

---

### Task 2: sync_roster＋名寄せ＋KAKENガード＋CLI組込み

**Files:**
- Modify: `collector/roster.py`（sync_roster / match_roster 追記）
- Modify: `collector/kaken.py`（match_members に is_official_roster ガード）
- Modify: `scripts/sync.py`（rosterステージ追加）
- Test: `tests/test_roster_sync.py`, `tests/test_kaken_sync.py`（ガードのテスト追記）

**Interfaces:**
- Consumes: Task 1の全て、`collector.nameutil`
- Produces:
  - `collector.roster.sync_roster(session, client, today) -> int` — 部局列挙→全ページ収集（profile_id重複は先勝ち）→全洗い替え（0件ガード）→`sync_state`(source="roster")
  - `collector.roster.match_roster(session) -> int` — 二重ブリッジ・一意のみ・同一研究者への複数主張は全巻き戻し。確定者のresearchersを更新、旧roster研究者のdepartment/position/フラグをクリア（name_jaは残す）
  - `match_members`（kaken）が is_official_roster=True の研究者の name_ja に触れない

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_roster_sync.py`）

```python
import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.roster import match_roster, sync_roster
from db.models import Researcher, Roster, SyncState, get_engine

TODAY = datetime.date(2026, 7, 2)

HOME = '<a href="/search?m=affiliation&l=ja&a2=0000001&s=1&o=affiliation">大学院文学研究科</a>'
LIST1 = """
<div>2件中</div>
<div class="card-body result">
  <div class="name-kna">ヤマダ　タロウ</div>
  <div class="name-gng"><a href="/html/111_ja.html">山田　太郎</a></div>
  <div class="name-title">教授</div>
</div>
<div class="card-body result">
  <div class="name-kna">スズキ　ハナコ</div>
  <div class="name-gng"><a href="/html/222_ja.html">鈴木　花子</a></div>
  <div class="name-title">准教授</div>
</div>
"""


class FakeRosterClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self.pages[len(self.calls) - 1]


def _researcher(id_, name, **kw):
    return Researcher(openalex_id=id_, display_name=name, h_index=1,
                      works_count=1, raw_json="{}", updated_at="", **kw)


def test_sync_roster_stores_members():
    engine = get_engine(":memory:")
    client = FakeRosterClient([HOME, LIST1])
    with Session(engine) as s:
        n = sync_roster(s, client, today=TODAY)
        assert n == 2
        r = s.get(Roster, "111")
        assert r.name_kanji == "山田 太郎"
        assert r.division == "大学院文学研究科"
        assert r.position == "教授"
        assert s.get(SyncState, "roster").last_synced_at == "2026-07-02"
    assert client.calls[1][1]["a2"] == "0000001"
    assert client.calls[1][1]["pp"] == 100


def test_sync_roster_empty_keeps_existing(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Roster(profile_id="OLD", name_kanji="既存 人", division="旧",
                     updated_at=""))
        s.commit()
        client = FakeRosterClient(["<html>リンクなし</html>"])
        with caplog.at_level("WARNING"):
            assert sync_roster(s, client, today=TODAY) == 0
        assert s.get(Roster, "OLD") is not None


def test_match_roster_dual_bridge_and_apply():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([
            _researcher("A1", "Taro Yamada"),                    # カナで一致
            _researcher("A2", "X Y", name_ja="鈴木 花子"),        # 漢字で一致
            _researcher("A3", "Ichiro Tanaka"),
            _researcher("A4", "Ichiro Tanaka"),                  # 同名→曖昧
        ])
        s.add_all([
            Roster(profile_id="111", name_kanji="山田 太郎",
                   name_kana="ヤマダ　タロウ", position="教授",
                   division="大学院文学研究科", updated_at=""),
            Roster(profile_id="222", name_kanji="鈴木 花子",
                   name_kana=None, position="准教授",
                   division="大学院医学研究科", updated_at=""),
            Roster(profile_id="333", name_kanji="田中 一郎",
                   name_kana="タナカ　イチロウ", position="講師",
                   division="大学院文学研究科", updated_at=""),
        ])
        s.commit()

        n = match_roster(s)
        assert n == 2
        a1 = s.get(Researcher, "A1")
        assert a1.department == "大学院文学研究科"
        assert a1.position == "教授"
        assert a1.name_ja == "山田 太郎"
        assert a1.is_official_roster is True
        a2 = s.get(Researcher, "A2")
        assert a2.department == "大学院医学研究科"
        assert s.get(Roster, "333").matched_researcher_id is None  # 曖昧
        assert s.get(Researcher, "A3").department is None


def test_match_roster_conflict_and_clear():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # 以前rosterマッチだった研究者（今回のrosterに居ない）→ クリア
        s.add(_researcher("A9", "Old Member", name_ja="昔 の人",
                          department="旧部局", position="教授",
                          is_official_roster=True))
        # 複数roster行が同一研究者を主張 → 双方未マッチ
        s.add(_researcher("A1", "Taro Yamada"))
        s.add_all([
            Roster(profile_id="111", name_kanji="山田 太郎",
                   name_kana="ヤマダ　タロウ", division="D1", updated_at=""),
            Roster(profile_id="999", name_kanji="山田 太朗",
                   name_kana="ヤマダ　タロウ", division="D2", updated_at=""),
        ])
        s.commit()

        n = match_roster(s)
        assert n == 0
        assert s.get(Roster, "111").matched_researcher_id is None
        assert s.get(Roster, "999").matched_researcher_id is None
        a9 = s.get(Researcher, "A9")
        assert a9.department is None and a9.is_official_roster is False
        assert a9.name_ja == "昔 の人"   # name_jaは残す
```

`tests/test_kaken_sync.py` にガードのテストを追加:

```python
def test_match_members_respects_official_roster():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A1", display_name="Taro Yamada",
                         name_ja="公式 名前", is_official_roster=True,
                         h_index=1, works_count=1, raw_json="{}",
                         updated_at=""))
        s.add(Grant(award_id="G1", title="t", total_amount=0,
                    raw_json="{}", updated_at=""))
        s.add(GrantMember(award_id="G1", erad_id="E1", name_kanji="山田 太郎",
                          name_kana="ヤマダ　タロウ", role="principal",
                          institution="大阪公立大学"))
        s.commit()
        match_members(s)
        a1 = s.get(Researcher, "A1")
        assert a1.name_ja == "公式 名前"   # 上書きしない
        # マッチ自体は成立してよい（grant紐付けは有効）
        assert s.get(GrantMember, ("G1", "E1")).matched_researcher_id == "A1"
```

（このファイルの既存importに `Researcher` 等が無ければ追加）

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_roster_sync.py tests/test_kaken_sync.py -v` → FAIL

- [ ] **Step 3: sync_roster / match_roster 実装**（`collector/roster.py` に追記）

```python
import datetime
from collections import defaultdict

from sqlalchemy import delete, select

from collector.nameutil import kana_part_variants, normalize_name
from db.models import Researcher, Roster, SyncState


def sync_roster(session, client, today: datetime.date) -> int:
    home = client.fetch("/")
    divisions = parse_divisions(home)
    if not divisions:
        logger.warning("roster: 部局リンクが見つからないためスキップ（既存データ保持）")
        return 0
    rows: dict[str, dict] = {}
    for code, label in divisions:
        start = 1
        while True:
            html = client.fetch("/search", params={
                "m": "affiliation", "l": "ja", "a2": code,
                "s": start, "o": "affiliation", "pp": PAGE_SIZE})
            members, total = parse_list_page(html)
            for m in members:
                if m["profile_id"] not in rows:
                    rows[m["profile_id"]] = {**m, "division": label}
            start += PAGE_SIZE
            if start > total or not members:
                break
    if not rows:
        logger.warning("roster: 0件のため洗い替えをスキップ（既存データ保持）")
        return 0
    session.execute(delete(Roster))
    for row in rows.values():
        session.add(Roster(**row, updated_at=today.isoformat()))
    session.merge(SyncState(source="roster", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("roster sync done: %d人 / %d部局", len(rows), len(divisions))
    return len(rows)


def match_roster(session) -> int:
    name_index: dict[str, set[str]] = defaultdict(set)
    kanji_index: dict[str, set[str]] = defaultdict(set)
    for rid, display_name, name_ja in session.execute(
            select(Researcher.openalex_id, Researcher.display_name,
                   Researcher.name_ja)):
        name_index[normalize_name(display_name)].add(rid)
        if name_ja:
            kanji_index[name_ja].add(rid)

    entries = list(session.scalars(select(Roster)))
    proposals: dict[str, list] = defaultdict(list)  # rid -> [Roster, ...]
    for entry in entries:
        entry.matched_researcher_id = None
        candidates: set[str] = set(kanji_index.get(entry.name_kanji, set()))
        if entry.name_kana:
            parts = entry.name_kana.replace("　", " ").split()
            if len(parts) == 2:
                for fam in kana_part_variants(parts[0]):
                    for giv in kana_part_variants(parts[1]):
                        candidates |= name_index.get(
                            normalize_name(f"{giv} {fam}"), set())
                        candidates |= name_index.get(
                            normalize_name(f"{fam} {giv}"), set())
        if len(candidates) == 1:
            proposals[candidates.pop()].append(entry)

    matched = 0
    matched_rids: set[str] = set()
    for rid, claimants in proposals.items():
        if len(claimants) != 1:
            continue  # 学内同姓同名の衝突 → 全て未マッチのまま
        entry = claimants[0]
        researcher = session.get(Researcher, rid)
        if researcher is None:
            continue
        entry.matched_researcher_id = rid
        researcher.name_ja = entry.name_kanji
        researcher.department = entry.division
        researcher.position = entry.position
        researcher.is_official_roster = True
        matched_rids.add(rid)
        matched += 1

    # 以前rosterマッチだったが今回外れた研究者のクリア（name_jaは残す）
    for researcher in session.scalars(
            select(Researcher).where(
                Researcher.is_official_roster.is_(True))):
        if researcher.openalex_id not in matched_rids:
            researcher.department = None
            researcher.position = None
            researcher.is_official_roster = False
    session.commit()
    logger.info("roster名寄せ: %d人を確定", matched)
    return matched
```

- [ ] **Step 4: KAKENガード**（`collector/kaken.py` `match_members`）

name_ja の設定ループと、古いname_jaのクリアループの両方に is_official_roster ガードを入れる:
- 設定側: `researcher = session.get(Researcher, rid)` の後、`if researcher is None or researcher.is_official_roster: continue`（matched_researcher_id の設定はガードの**前**に行う——grant紐付け自体は有効のまま）
- クリア側: クリア対象のselect/ループに `Researcher.is_official_roster.is_(False)`（またはループ内スキップ）を追加

（現行実装の構造に合わせて適用。matched_researcher_id は従来通り設定されることをテストが検証する）

- [ ] **Step 5: CLI組込み**（`scripts/sync.py`）

importに追加:

```python
from collector.roster import RosterClient, match_roster, sync_roster
```

kakenブロックの直後（`compute_metrics` の前）に追加:

```python
        try:
            n_r = sync_roster(session, RosterClient(), today=today)
            n_rm = match_roster(session)
            logger.info("roster: %d人 matched=%d", n_r, n_rm)
        except Exception:
            logger.exception("roster同期に失敗（他ステージは継続）")
```

- [ ] **Step 6: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 7: Commit**

```bash
git add collector/roster.py collector/kaken.py scripts/sync.py tests/test_roster_sync.py tests/test_kaken_sync.py
git commit -m "feat: 公式総覧の同期と二重ブリッジ名寄せ（公式総覧優先ガード付き）"
```

---

### Task 3: Web（部局フィルタ＋/departments＋詳細表示）

**Files:**
- Modify: `web/queries.py`（ranking department対応・departments_list・department_stats）
- Modify: `web/app.py`（ランキングparam・/departmentsルート）
- Create: `web/templates/departments.html`
- Modify: `web/templates/ranking.html`, `researcher.html`, `base.html`
- Modify: `tests/conftest.py`（A1/A3にdepartment等をseed）
- Test: `tests/test_web_queries.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 1-2（Researcher.department等）
- Produces:
  - `ranking(session, sort="fwci_total", min_works=1, page=1, department=None)` — department指定時はその部局のみ、total/total_allも部局スコープ
  - `departments_list(session) -> list[str]`
  - `department_stats(session) -> list[dict]` — キー: `department/members/works/citations/fwci_total/works_per_capita/fwci_per_capita/top10/kaken_amount`。fwci_per_capita降順
  - `GET /departments`、ランキングの部局ドロップダウン、詳細ページの部局・職位表示、navの「部局」

- [ ] **Step 1: conftest seed拡張**

Researcher A1 に `name_ja="山田 太郎", department="大学院医学研究科", position="教授", is_official_roster=True,` を追加（`orcid=` の後）。A3 に `department="大学院情報学研究科", position="講師", is_official_roster=True,` を追加。A2/A4はそのまま。

**注意**: A1に `name_ja` が入ることで、氏名表示が「山田 太郎」になり既存テストの `"Taro Yamada"` アサーションが壊れる。`tests/test_web.py` / `tests/test_web_queries.py` 内の **画面表示**を見る `"Taro Yamada"` は `"山田 太郎"` に置換する（`display_name` を直接見るqueriesテスト、検索クエリ `q=yama` のヒット判定は `display_name` 対象なので検索自体は成立。ただし結果表示名は山田 太郎になる）。機械的に全置換せず、各アサーションの意図（表示名か生データか）を確認して直すこと。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_web_queries.py` に追加（importへ `departments_list, department_stats` 追加）:

```python
def test_ranking_department_filter(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows, total, total_all = ranking(s, department="大学院医学研究科",
                                         min_works=0)
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]
        assert total == 1 and total_all == 1
        rows, _, _ = ranking(s, department="存在しない部局", min_works=0)
        assert rows == []


def test_departments_list_and_stats(seeded_db_path):
    with _session(seeded_db_path) as s:
        assert departments_list(s) == ["大学院医学研究科", "大学院情報学研究科"]
        stats = department_stats(s)
        assert [d["department"] for d in stats] == [
            "大学院医学研究科", "大学院情報学研究科"]  # fwci_per_capita 35.0 > 19.8
        med = stats[0]
        assert med["members"] == 1
        assert med["works"] == 10
        assert med["fwci_per_capita"] == 35.0
        assert med["kaken_amount"] == 75_000_000
```

`tests/test_web.py` に追加:

```python
def test_ranking_department_dropdown_and_filter(client):
    body = client.get("/").text
    assert 'name="department"' in body
    assert "大学院医学研究科" in body
    filtered = client.get("/?department=大学院医学研究科&min_works=0").text
    assert "山田 太郎" in filtered
    assert "Hanako Suzuki" not in filtered
    assert "大学院医学研究科: 全1人中 1人を表示" in filtered
    # ソートリンクがdepartmentを保持
    assert "sort=total_citations&min_works=0&department=" in filtered


def test_ranking_invalid_department_falls_back(client):
    body = client.get("/?department=偽の部局&min_works=0").text
    assert "Hanako Suzuki" in body  # 全学表示


def test_departments_page(client):
    body = client.get("/departments").text
    assert "大学院医学研究科" in body and "大学院情報学研究科" in body
    assert body.index("大学院医学研究科") < body.index("大学院情報学研究科")
    assert "35.00" in body          # 人あたりFWCI合計
    assert "名寄せできた研究者のみ" in body
    assert "/?department=" in body  # 部局リンク


def test_researcher_detail_shows_department(client):
    body = client.get("/researchers/A1").text
    assert "大学院医学研究科" in body and "教授" in body
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web.py tests/test_web_queries.py -v` → FAIL

- [ ] **Step 4: queries実装**（`web/queries.py`）

`ranking` を差し替え:

```python
def ranking(session, sort="fwci_total", min_works=1, page=1, department=None):
    col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_total)
    count_q = (select(func.count())
               .select_from(ResearcherMetrics)
               .join(Researcher,
                     Researcher.openalex_id == ResearcherMetrics.researcher_id))
    rows_q = (select(Researcher, ResearcherMetrics)
              .join(ResearcherMetrics,
                    ResearcherMetrics.researcher_id == Researcher.openalex_id))
    if department:
        count_q = count_q.where(Researcher.department == department)
        rows_q = rows_q.where(Researcher.department == department)
    total_all = session.scalar(count_q)
    cond = ResearcherMetrics.works_count_3y >= min_works
    total = session.scalar(count_q.where(cond))
    rows = session.execute(
        rows_q.where(cond)
        # SQLiteはNULLを最小値として扱うため、DESCでNULLは自然に末尾になる
        .order_by(col.desc(), Researcher.openalex_id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return rows, total, total_all
```

末尾に追加:

```python
def departments_list(session):
    return list(session.scalars(
        select(Researcher.department)
        .where(Researcher.department.is_not(None))
        .distinct()
        .order_by(Researcher.department)))


def department_stats(session):
    rows = session.execute(
        select(Researcher.department,
               func.count(),
               func.sum(ResearcherMetrics.works_count_3y),
               func.sum(ResearcherMetrics.total_citations),
               func.sum(ResearcherMetrics.fwci_total),
               func.sum(ResearcherMetrics.top10pct_count),
               func.sum(ResearcherMetrics.kaken_total_amount))
        .join(ResearcherMetrics,
              ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(Researcher.department.is_not(None))
        .group_by(Researcher.department)
    ).all()
    stats = []
    for dept, n, works, cites, fwci, top10, kaken in rows:
        stats.append({
            "department": dept,
            "members": n,
            "works": works or 0,
            "citations": cites or 0,
            "fwci_total": round(fwci or 0, 2),
            "works_per_capita": round((works or 0) / n, 2),
            "fwci_per_capita": round((fwci or 0) / n, 2),
            "top10": top10 or 0,
            "kaken_amount": kaken or 0,
        })
    stats.sort(key=lambda item: item["fwci_per_capita"], reverse=True)
    return stats
```

- [ ] **Step 5: app実装**（`web/app.py`）

`ranking_page` を差し替え（departmentパラメタ・検証・context追加）:

```python
    @app.get("/", response_class=HTMLResponse)
    def ranking_page(request: Request, sort: str = "fwci_total",
                     min_works: str = "1", page: str = "1",
                     department: str = ""):
        sort_key = sort if sort in queries.SORT_COLUMNS else "fwci_total"
        mw = _int_param(min_works, 1)
        pg = _int_param(page, 1, minimum=1)
        with Session(engine) as session:
            departments = queries.departments_list(session)
            dept = department if department in departments else None
            rows, total, total_all = queries.ranking(
                session, sort_key, mw, pg, department=dept)
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "ranking.html", {
            "rows": rows, "total": total, "total_all": total_all,
            "sort": sort_key, "min_works": mw, "page": pg,
            "page_size": queries.PAGE_SIZE, "synced": synced,
            "departments": departments, "department": dept,
        })
```

`compare_page` の後にルート追加:

```python
    @app.get("/departments", response_class=HTMLResponse)
    def departments_page(request: Request):
        with Session(engine) as session:
            stats = queries.department_stats(session)
            matched = sum(item["members"] for item in stats)
            total_count = session.scalar(
                select(func.count()).select_from(ResearcherMetrics))
            synced = queries.last_synced(session)
        return templates.TemplateResponse(request, "departments.html", {
            "stats": stats, "matched": matched, "total_count": total_count,
            "synced": synced,
        })
```

（`web/app.py` のimportに `from sqlalchemy import func, select` と `from db.models import ResearcherMetrics` を追加。既存importと重複しないよう整理）

- [ ] **Step 6: テンプレート**

`web/templates/ranking.html`:
- 冒頭（`{% block content %}` 直後）に追加: `{% set qs_dept = '&department=' ~ (department|urlencode) if department else '' %}`
- `<h2>` を差し替え: `<h2>ランキング <span class="total">{% if department %}{{ department }}: {% endif %}全{{ total_all }}人中 {{ total }}人を表示</span></h2>`
- controlsフォームの `<label>最低論文数...` の前に追加:

```html
  <label>部局:
    <select name="department">
      <option value="">全学</option>
      {% for d in departments %}
      <option value="{{ d }}" {% if d == department %}selected{% endif %}>{{ d }}</option>
      {% endfor %}
    </select>
  </label>
```

- thead内の全ソートリンク（5箇所）とページャの2リンクのhref末尾に `{{ qs_dept }}` を追加（例: `href="/?sort=fwci_total&min_works={{ min_works }}{{ qs_dept }}"`）

`web/templates/base.html`: navの「比較」の後に `<a href="/departments">部局</a>` を追加。

`web/templates/researcher.html`: `<p class="links">` の前に追加:

```html
{% if r.department %}<p class="affil">{{ r.department }}{% if r.position %}・{{ r.position }}{% endif %}</p>{% endif %}
```

`web/templates/departments.html` 新規:

```html
{% extends "base.html" %}
{% block title %}部局間比較 - OMU研究者比較{% endblock %}
{% block content %}
<h2>部局間比較</h2>
<p class="notice">公式総覧と名寄せできた研究者のみの集計（全{{ total_count }}人中 {{ matched }}人）。規模の異なる部局は「人あたり」列で比較してください。</p>
<table>
<thead><tr>
  <th>部局</th><th>人数</th><th>3年論文数</th><th>人あたり論文</th>
  <th>総被引用</th><th>FWCI合計</th><th>人あたりFWCI合計</th>
  <th>top10%</th><th>科研費総額</th>
</tr></thead>
<tbody>
{% for s in stats %}
<tr>
  <td><a href="/?department={{ s.department|urlencode }}">{{ s.department }}</a></td>
  <td>{{ s.members }}</td>
  <td>{{ s.works }}</td>
  <td>{{ "%.2f"|format(s.works_per_capita) }}</td>
  <td>{{ "{:,}".format(s.citations) }}</td>
  <td>{{ "%.2f"|format(s.fwci_total) }}</td>
  <td><strong>{{ "%.2f"|format(s.fwci_per_capita) }}</strong></td>
  <td>{{ s.top10 }}</td>
  <td>{{ s.kaken_amount|man }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endblock %}
```

`web/static/style.css` に追記:

```css
.affil { color: var(--muted); margin: -0.5rem 0 0.5rem; font-size: 0.9rem; }
```

- [ ] **Step 7: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine（Step 1の注意に従い、氏名表示のアサーション修正を含む）

- [ ] **Step 8: Commit**

```bash
git add web tests
git commit -m "feat: 部局フィルタと部局間比較ページ"
```

---

### Task 4: 実クロール＋検証

**Files:** 実行のみ（構造相違があれば `collector/roster.py`＋fixture修正）

- [ ] **Step 1: 実同期（実API＋実クロール、timeout 600000ms）**

```bash
uv run python scripts/sync.py
```

Expected: 既存の `done: authors=... kaken: ...` に加えて `roster: <3000〜4000>人 matched=<数百〜千人規模>`、警告なし。rosterが0件警告になったら実HTMLの構造相違を調査し、パーサ＋fixtureを実構造へ修正して再実行（変更内容は報告に明記）

- [ ] **Step 2: スポットチェック**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
q = lambda sql: c.execute(sql).fetchall()
print('roster:', q('SELECT COUNT(*) FROM roster'))
print('divisions:', q('SELECT COUNT(DISTINCT division) FROM roster'))
print('matched:', q('SELECT COUNT(*) FROM roster WHERE matched_researcher_id IS NOT NULL'))
print('dept researchers:', q('SELECT COUNT(*) FROM researchers WHERE department IS NOT NULL'))
print('name_ja:', q('SELECT COUNT(*) FROM researchers WHERE name_ja IS NOT NULL'))
print('by dept:', q('SELECT department, COUNT(*) FROM researchers WHERE department IS NOT NULL GROUP BY department ORDER BY 2 DESC LIMIT 5'))
"
```

Expected: roster 3,000人前後・部局数十・matchedとdept researchersが一致・name_jaがKAKEN時代（612）から大幅増

- [ ] **Step 3: 実表示確認（port 8199）**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
curl -s "http://127.0.0.1:8199/departments" | grep -oE "全[0-9,]+人中 [0-9,]+人" | head -1
DEPT=$(curl -s "http://127.0.0.1:8199/departments" | grep -oP '/\?department=\K[^"]+' | head -1)
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8199/?department=${DEPT}"
kill %1
```

Expected: 名寄せ内訳と 200

- [ ] **Step 4: 変更があればCommit・なければ確認のみ**

```bash
git status --short   # .env / db が出ないこと
```

---

## 完了条件

- 全テストPASS・実クロールで roster/部局/名寄せが入り、`/departments` と部局フィルタが実データで動く
