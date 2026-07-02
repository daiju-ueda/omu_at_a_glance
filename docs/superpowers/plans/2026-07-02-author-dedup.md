# OpenAlex重複著者の解決 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 同一人物の複数OpenAlex著者レコードを証拠ベースで論理統合し、メトリクス・名寄せ・表示を正準レコードに畳む。

**Architecture:** `collector/dedup.py`（union-find＋証拠ルール）が `researchers.canonical_id` を毎sync全再計算。metricsは著者IDを正準へ写像してDISTINCT work集計。名寄せ索引と検索は正準のみ。エイリアスの詳細URLは正準へ302。

**Tech Stack:** 既存のみ。

**設計書:** `docs/superpowers/specs/2026-07-02-author-dedup-design.md`

## Global Constraints

- マージは同一 `normalize_name(display_name)` グループ内のみ。証拠: 同一非NULL ORCID / 同一work共有 / ウィンドウ内共著者の重なり≥2（定数 `COAUTHOR_OVERLAP_MIN = 2`）。**異なる非NULL ORCIDのペアは結合しない**うえ、推移的に混ざったクラスタ（内部に異なる非NULL ORCIDが複数）は**解散して一切マージしない**（警告ログ）
- 正準 = クラスタ内 works_count（全期間）最大、同数は openalex_id 辞書順で先
- `canonical_id` はNULL=正準、非NULL=正準IDを指す1段のみ。毎sync全再計算（冪等）
- メトリクスは正準のみが行を持つ。work集計はDISTINCT（同一workに複数エイリアス著者行がある場合、first/correspondingはOR）
- 名寄せ（kaken/roster）の索引・書き込み先は正準のみ
- エイリアスのname_ja/department/position/フラグは正準へ引き継ぎ（正準側未設定時のみコピー）後クリア
- dedup失敗はログして他ステージ継続（sync全体を止めない）
- スキーマ変更（canonical_id）は確立済みのDB再構築運用で反映（Task 5）

---

### Task 1: canonical_id＋dedupエンジン

**Files:**
- Modify: `db/models.py`（Researcherに1列）
- Create: `collector/dedup.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Produces:
  - `Researcher.canonical_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)`（`is_official_roster` 行の直後）
  - `collector.dedup.apply_dedup(session, today=None) -> int` — エイリアス化した件数を返す

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_dedup.py`）

```python
import datetime

from sqlalchemy.orm import Session

from collector.dedup import apply_dedup
from db.models import Authorship, Researcher, Work, get_engine

TODAY = datetime.date(2026, 7, 2)


def _r(id_, name, orcid=None, works_count=10, **kw):
    return Researcher(openalex_id=id_, display_name=name, orcid=orcid,
                      h_index=1, works_count=works_count, raw_json="{}",
                      updated_at="", **kw)


def _w(id_):
    return Work(openalex_id=id_, title=id_, publication_date="2024-06-01",
                cited_by_count=0, is_top1pct=False, is_top10pct=False,
                is_oa=False, raw_json="{}", updated_at="")


def _a(work, author):
    return Authorship(work_id=work, author_id=author,
                      author_position="middle", is_corresponding=False)


def _setup(session, researchers, works, auths):
    session.add_all(researchers)
    session.add_all(works)
    session.add_all(auths)
    session.commit()


def test_merge_by_same_orcid():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Hiroaki Nakamura", orcid="0-1", works_count=100),
                   _r("A2", "Hiroaki Nakamura", orcid="0-1", works_count=5)],
               [], [])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A1").canonical_id is None      # 正準（works多）
        assert s.get(Researcher, "A2").canonical_id == "A1"


def test_never_merge_different_orcid_even_with_shared_coauthors():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # 共著者2人の重なりがあってもORCID相違なら結合しない
        _setup(s, [_r("A1", "Taro Sato", orcid="0-1"),
                   _r("A2", "Taro Sato", orcid="0-2"),
                   _r("C1", "Coauthor One"), _r("C2", "Coauthor Two")],
               [_w("W1"), _w("W2")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2")])
        assert apply_dedup(s, TODAY) == 0
        assert s.get(Researcher, "A1").canonical_id is None
        assert s.get(Researcher, "A2").canonical_id is None


def test_merge_by_coauthor_overlap_threshold():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A1/A2: 共通共著者2人 → 結合。A3: 共通1人のみ → 結合しない
        _setup(s, [_r("A1", "Jiro Ito", works_count=50),
                   _r("A2", "Jiro Ito", works_count=10),
                   _r("A3", "Jiro Ito", works_count=5),
                   _r("C1", "Co One"), _r("C2", "Co Two")],
               [_w("W1"), _w("W2"), _w("W3")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2"),
                _a("W3", "A3"), _a("W3", "C1")])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
        assert s.get(Researcher, "A3").canonical_id is None


def test_merge_by_shared_work_and_no_cross_name_merge():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Ken Abe", works_count=50),
                   _r("A2", "Ken Abe", works_count=10),
                   _r("B1", "Ken Aoki", works_count=99)],  # 別名は対象外
               [_w("W1")],
               [_a("W1", "A1"), _a("W1", "A2"), _a("W1", "B1")])
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
        assert s.get(Researcher, "B1").canonical_id is None


def test_cluster_with_multiple_orcids_dissolved(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        # A1(ORCID x)–A2(無)–A3(ORCID y): A2経由で推移的につながるが解散
        _setup(s, [_r("A1", "Yu Mori", orcid="0-x"),
                   _r("A2", "Yu Mori"),
                   _r("A3", "Yu Mori", orcid="0-y"),
                   _r("C1", "Co One"), _r("C2", "Co Two")],
               [_w("W1"), _w("W2"), _w("W3")],
               [_a("W1", "A1"), _a("W1", "C1"), _a("W1", "C2"),
                _a("W2", "A2"), _a("W2", "C1"), _a("W2", "C2"),
                _a("W3", "A3"), _a("W3", "C1"), _a("W3", "C2")])
        with caplog.at_level("WARNING"):
            assert apply_dedup(s, TODAY) == 0
        assert any("解散" in r.message for r in caplog.records)


def test_alias_attributes_handed_over_and_idempotent():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        _setup(s, [_r("A1", "Rin Kato", orcid="0-1", works_count=100),
                   _r("A2", "Rin Kato", orcid="0-1", works_count=5,
                      name_ja="加藤 凛", department="大学院医学研究科",
                      position="教授", is_official_roster=True)],
               [], [])
        apply_dedup(s, TODAY)
        canon = s.get(Researcher, "A1")
        alias = s.get(Researcher, "A2")
        assert canon.name_ja == "加藤 凛"
        assert canon.department == "大学院医学研究科"
        assert canon.is_official_roster is True
        assert alias.name_ja is None and alias.department is None
        assert alias.is_official_roster is False
        # 冪等
        assert apply_dedup(s, TODAY) == 1
        assert s.get(Researcher, "A2").canonical_id == "A1"
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_dedup.py -v` → FAIL（import error）

- [ ] **Step 3: モデル列追加**（`db/models.py` Researcher、`is_official_roster` 行の直後）

```python
    canonical_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True)
```

- [ ] **Step 4: 実装**（`collector/dedup.py` 新規）

```python
import datetime
import logging
from collections import defaultdict

from sqlalchemy import select

from collector.nameutil import normalize_name
from collector.sync import window_start
from db.models import Authorship, Researcher, Work

logger = logging.getLogger(__name__)

COAUTHOR_OVERLAP_MIN = 2


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def apply_dedup(session, today: datetime.date | None = None) -> int:
    members_all = session.execute(
        select(Researcher.openalex_id, Researcher.display_name,
               Researcher.orcid, Researcher.works_count)).all()
    groups: dict[str, list] = defaultdict(list)
    for m in members_all:
        groups[normalize_name(m.display_name)].append(m)

    start = window_start(today or datetime.date.today())
    works_by_author: dict[str, set[str]] = defaultdict(set)
    authors_by_work: dict[str, set[str]] = defaultdict(set)
    for work_id, author_id in session.execute(
            select(Authorship.work_id, Authorship.author_id)
            .join(Work, Work.openalex_id == Authorship.work_id)
            .where(Work.publication_date >= start)):
        works_by_author[author_id].add(work_id)
        authors_by_work[work_id].add(author_id)

    coauthor_cache: dict[str, set[str]] = {}

    def coauthors(aid: str) -> set[str]:
        if aid not in coauthor_cache:
            out: set[str] = set()
            for w in works_by_author.get(aid, set()):
                out |= authors_by_work[w]
            out.discard(aid)
            coauthor_cache[aid] = out
        return coauthor_cache[aid]

    canonical_map: dict[str, str] = {}
    for name, members in groups.items():
        if len(members) < 2:
            continue
        ids = [m.openalex_id for m in members]
        orcid_of = {m.openalex_id: m.orcid for m in members}
        uf = _UnionFind(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                oa, ob = orcid_of[a], orcid_of[b]
                if oa and ob and oa != ob:
                    continue  # 分離証拠が最優先
                if (oa and ob and oa == ob) \
                        or (works_by_author[a] & works_by_author[b]) \
                        or len(coauthors(a) & coauthors(b)) >= COAUTHOR_OVERLAP_MIN:
                    uf.union(a, b)
        clusters: dict[str, list] = defaultdict(list)
        for m in members:
            clusters[uf.find(m.openalex_id)].append(m)
        for cluster in clusters.values():
            if len(cluster) < 2:
                continue
            orcids = {m.orcid for m in cluster if m.orcid}
            if len(orcids) > 1:
                logger.warning(
                    "dedup: 異なるORCIDを含むクラスタを解散 (%s, %d人)",
                    name, len(cluster))
                continue
            canonical = sorted(
                cluster, key=lambda m: (-m.works_count, m.openalex_id))[0]
            for m in cluster:
                if m.openalex_id != canonical.openalex_id:
                    canonical_map[m.openalex_id] = canonical.openalex_id

    # 全再計算・冪等: 全行のcanonical_idを更新
    for researcher in session.scalars(select(Researcher)):
        researcher.canonical_id = canonical_map.get(researcher.openalex_id)

    # エイリアス属性の引き継ぎ（正準未設定時のみ）とクリア
    for alias_id, canon_id in canonical_map.items():
        alias = session.get(Researcher, alias_id)
        canon = session.get(Researcher, canon_id)
        if alias is None or canon is None:
            continue
        if alias.name_ja and not canon.name_ja:
            canon.name_ja = alias.name_ja
        if alias.department and not canon.department:
            canon.department = alias.department
            canon.position = alias.position
            canon.is_official_roster = alias.is_official_roster
        alias.name_ja = None
        alias.department = None
        alias.position = None
        alias.is_official_roster = False
    session.commit()
    logger.info("dedup: %d件をエイリアス化", len(canonical_map))
    return len(canonical_map)
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_dedup.py -v` → PASS (6 tests)。全suite PASS

- [ ] **Step 6: Commit**

```bash
git add db/models.py collector/dedup.py tests/test_dedup.py
git commit -m "feat: 証拠ベースの重複著者統合エンジン（canonical_id）"
```

---

### Task 2: メトリクスの正準集計

**Files:**
- Modify: `collector/metrics.py`
- Test: `tests/test_metrics.py`（正準集計テスト追加）

**Interfaces:**
- Consumes: `Researcher.canonical_id`
- Produces: `compute_metrics` がエイリアスの業績を正準へ合算（DISTINCT work・フラグOR・共著者正準化）し、**エイリアスは researcher_metrics 行を持たない**

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_metrics.py` に追記）

```python
def test_compute_metrics_canonicalizes_aliases():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([_researcher("A1"), _researcher("A2")])
        alias = _researcher("A1b")
        alias.canonical_id = "A1"
        s.add(alias)
        s.add_all([
            _work("W1", "2024-01-01", 10, 2.0, True),
            _work("W2", "2024-02-01", 4, 1.0, False),
            _work("W3", "2024-03-01", 6, None, False),
        ])
        s.add_all([
            _auth("W1", "A1", position="first", corresponding=True),
            _auth("W2", "A1b"),                 # エイリアスの業績
            _auth("W3", "A1"),
            _auth("W3", "A1b", position="first"),  # 同一workに両IDが載る稀ケース
            _auth("W1", "A2"),
        ])
        s.commit()

        n = compute_metrics(s, TODAY)
        assert n == 2                       # A1とA2のみ（A1bは行なし）
        assert s.get(ResearcherMetrics, "A1b") is None
        m1 = s.get(ResearcherMetrics, "A1")
        assert m1.works_count_3y == 3       # W1+W2+W3（W3は1回だけ）
        assert m1.total_citations == 20
        assert m1.first_author_count == 2   # W1 + W3（エイリアス側first→OR）
        assert m1.corresponding_count == 1
        m2 = s.get(ResearcherMetrics, "A2")
        assert m2.unique_coauthors == 1     # A1bはA1に畳まれ、A1のみ
```

（`_researcher` / `_work` / `_auth` は既存ヘルパー。`_auth` に位置/責任のキーワードが無ければ既存シグネチャに合わせて調整すること）

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_metrics.py -v` → FAIL

- [ ] **Step 3: 実装**（`collector/metrics.py` `compute_metrics` を再構成）

冒頭（delete後）にエイリアス写像を追加:

```python
    alias_map: dict[str, str] = {
        row.openalex_id: row.canonical_id
        for row in session.execute(
            select(Researcher.openalex_id, Researcher.canonical_id)
            .where(Researcher.canonical_id.is_not(None)))
    }

    def canon(author_id: str) -> str:
        return alias_map.get(author_id, author_id)
```

rowsの集計を(正準著者, work)単位に変更:

```python
    per_author_work: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (canon(row.author_id), row.openalex_id)
        agg = per_author_work.get(key)
        if agg is None:
            per_author_work[key] = {
                "row": row,
                "first": row.author_position == "first",
                "corresponding": bool(row.is_corresponding),
            }
        else:
            agg["first"] = agg["first"] or row.author_position == "first"
            agg["corresponding"] = (agg["corresponding"]
                                    or bool(row.is_corresponding))

    by_author: dict[str, list] = {}
    for (author_id, _work_id), agg in per_author_work.items():
        by_author.setdefault(author_id, []).append(agg)
```

by_work（共著者集合）も正準化:

```python
        by_work.setdefault(work_id, set()).add(canon(author_id))
```

研究者ループはエイリアスを除外し、item参照を `agg` 構造に合わせて変更:

```python
    for rid in session.scalars(select(Researcher.openalex_id)
                               .where(Researcher.canonical_id.is_(None))):
        items = by_author.get(rid, [])
        n_works = len(items)
        rows_only = [it["row"] for it in items]
        fwcis = [r.fwci for r in rows_only if r.fwci is not None]
        countable = [r for r in rows_only if not r.is_authors_truncated]
        divisors = [max(r.n_authors, 1) for r in countable]
        partners: set[str] = set()
        for r in rows_only:
            partners |= by_work.get(r.openalex_id, set())
        partners.discard(rid)
        ...
            first_author_count=sum(1 for it in items if it["first"]),
            corresponding_count=sum(1 for it in items if it["corresponding"]),
        ...
```

（fwci_mean/median/total、top10/top1、rate系、preprint等の既存集計は `rows_only` を、first/correspondingのみ `items` のOR済みフラグを使う。kaken集計の `matched_researcher_id` も `canon(...)` を通す）

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_metrics.py -v` → PASS。全suite PASS

- [ ] **Step 5: Commit**

```bash
git add collector/metrics.py tests/test_metrics.py
git commit -m "feat: メトリクスをエイリアス統合後の正準著者で集計"
```

---

### Task 3: 名寄せ・sync組込み

**Files:**
- Modify: `collector/kaken.py`, `collector/roster.py`（索引を正準のみに）
- Modify: `scripts/sync.py`（dedupステージ）
- Test: `tests/test_kaken_sync.py`, `tests/test_roster_sync.py`

**Interfaces:**
- Produces: `match_members` / `match_roster` の候補索引がエイリアスを除外。sync順: authors → works → **dedup** → kaken → roster → metrics

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_roster_sync.py` に追加:

```python
def test_match_roster_ignores_aliases():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(_researcher("A1", "Taro Yamada"))
        alias = _researcher("A1b", "Taro Yamada")
        alias.canonical_id = "A1"
        s.add(alias)
        s.add(Roster(profile_id="111", name_kanji="山田 太郎",
                     name_kana="ヤマダ　タロウ", position="教授",
                     division="大学院文学研究科", updated_at=""))
        s.commit()
        # エイリアス除外により候補は正準1人 → 一意マッチ成立
        assert match_roster(s) == 1
        assert s.get(Roster, "111").matched_researcher_id == "A1"
        assert s.get(Researcher, "A1").is_official_roster is True
        assert s.get(Researcher, "A1b").is_official_roster is False
```

`tests/test_kaken_sync.py` に追加:

```python
def test_match_members_ignores_aliases():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(openalex_id="A1", display_name="Taro Yamada",
                         h_index=1, works_count=1, raw_json="{}",
                         updated_at=""))
        alias = Researcher(openalex_id="A1b", display_name="Taro Yamada",
                           canonical_id="A1", h_index=1, works_count=1,
                           raw_json="{}", updated_at="")
        s.add(alias)
        s.add(Grant(award_id="G1", title="t", total_amount=0,
                    raw_json="{}", updated_at=""))
        s.add(GrantMember(award_id="G1", erad_id="E1", name_kanji="山田 太郎",
                          name_kana="ヤマダ　タロウ", role="principal",
                          institution="大阪公立大学"))
        s.commit()
        match_members(s)
        assert s.get(GrantMember, ("G1", "E1")).matched_researcher_id == "A1"
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_roster_sync.py tests/test_kaken_sync.py -v` → FAIL（候補2人で曖昧→未マッチのため）

- [ ] **Step 3: 実装**

`collector/kaken.py` `match_members` の索引ループのselectに `.where(Researcher.canonical_id.is_(None))` を追加。
`collector/roster.py` `match_roster` の索引ループのselectにも同様に追加（display_name/name_ja両索引とも正準のみ）。

`scripts/sync.py`: importに `from collector.dedup import apply_dedup` を追加し、`n_w = sync_works(...)` の直後（kakenブロックの前）に:

```python
        try:
            n_dedup = apply_dedup(session, today=today)
            logger.info("dedup: aliases=%d", n_dedup)
        except Exception:
            logger.exception("dedupに失敗（他ステージは継続）")
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 5: Commit**

```bash
git add collector/kaken.py collector/roster.py scripts/sync.py tests/test_kaken_sync.py tests/test_roster_sync.py
git commit -m "feat: dedupをsyncに組込み・名寄せ索引を正準のみに"
```

---

### Task 4: Web（リダイレクト・正準表示・検索フィルタ）

**Files:**
- Modify: `web/queries.py`（researcher_detailのworksをエイリアス込みに・searchを正準のみに）
- Modify: `web/app.py`（エイリアス→正準302）
- Test: `tests/test_web.py`, `tests/test_web_queries.py`, `tests/conftest.py`

**Interfaces:**
- Produces: `/researchers/{alias_id}` → 302 → 正準。正準詳細のworksはエイリアス分込み（work重複は1回）。検索結果は正準のみ

- [ ] **Step 1: conftest拡張**

`seeded_db_path` にエイリアスを1人追加（Researcher群の後）:

```python
        alias = Researcher(openalex_id="A1b", display_name="Taro Yamada",
                           orcid=None, h_index=2, works_count=3,
                           canonical_id="A1", raw_json="{}", updated_at="")
        s.add(alias)
```

W3（A1のmiddle work）のAuthorshipを1本エイリアスにも付ける:

```python
            Authorship(work_id="W3", author_id="A1b", author_position="first",
                       is_corresponding=False),
```

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_web_queries.py`:

```python
def test_search_excludes_aliases(seeded_db_path):
    with _session(seeded_db_path) as s:
        rows = search(s, "yamada")
        assert [r.Researcher.openalex_id for r in rows] == ["A1"]


def test_detail_includes_alias_works_once(seeded_db_path):
    with _session(seeded_db_path) as s:
        _, _, works = researcher_detail(s, "A1")
        ids = [row.Work.openalex_id for row in works]
        assert ids == ["W1", "W3"]      # W3はエイリアス行があっても1回
```

`tests/test_web.py`:

```python
def test_alias_redirects_to_canonical(client):
    resp = client.get("/researchers/A1b", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/researchers/A1"
```

- [ ] **Step 3: 失敗を確認** → FAIL

- [ ] **Step 4: 実装**

`web/queries.py` `search`: whereに `Researcher.canonical_id.is_(None),` を追加（or_の前の条件として）。

`web/queries.py` `researcher_detail`: works取得をエイリアス込み・work単位で重複排除に変更:

```python
    author_ids = [openalex_id] + list(session.scalars(
        select(Researcher.openalex_id)
        .where(Researcher.canonical_id == openalex_id)))
    raw_works = session.execute(
        select(Work, Authorship)
        .join(Authorship, Authorship.work_id == Work.openalex_id)
        .where(Authorship.author_id.in_(author_ids),
               Work.publication_date >= start)
        .order_by(Work.cited_by_count.desc(), Work.openalex_id)
    ).all()
    seen: set[str] = set()
    works = []
    for row in raw_works:
        if row.Work.openalex_id in seen:
            continue
        seen.add(row.Work.openalex_id)
        works.append(row)
```

`web/app.py` `researcher_page`: importに `RedirectResponse`（`fastapi.responses`）を追加し、冒頭でエイリアス判定:

```python
        with Session(engine) as session:
            researcher = session.get(Researcher, openalex_id)
            if researcher is not None and researcher.canonical_id:
                return RedirectResponse(
                    f"/researchers/{researcher.canonical_id}", status_code=302)
            ...
```

（`db.models.Researcher` のimportが `web/app.py` に無ければ追加。既存の `queries.researcher_detail` / `metric_ranks` 呼び出しはリダイレクト判定の後に実行）

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine（conftest変更による既存テストへの影響を確認・必要な追随修正はランキング等の件数系のみ想定: A1bはmetrics行が無いためランキング・検索に出ず、既存アサーションは原則不変のはず。壊れた場合は意図を確認して修正し報告に記載）

- [ ] **Step 6: Commit**

```bash
git add web tests
git commit -m "feat: エイリアスの正準リダイレクトと正準のみ検索・works統合表示"
```

---

### Task 5: DB再構築＋実データでのマージ品質検証

**Files:** 実行のみ

- [ ] **Step 1: 再構築＋full sync（実API＋実クロール、timeout 900000ms）**

```bash
rm -f db/researchers.db db/researchers.db-wal db/researchers.db-shm
uv run python scripts/sync.py
```

Expected: 既存ログに加えて `dedup: aliases=<数十〜数百>`、roster/kakenのmatchedが増加（roster 490→、kaken 1171→）

- [ ] **Step 2: マージ品質の検証（最重要）**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
q = lambda sql: c.execute(sql).fetchall()
print('aliases:', q('SELECT COUNT(*) FROM researchers WHERE canonical_id IS NOT NULL'))
print('大きいクラスタ:', q('''SELECT canonical_id, COUNT(*) FROM researchers WHERE canonical_id IS NOT NULL GROUP BY canonical_id ORDER BY 2 DESC LIMIT 5'''))
print('中村博亮チェック:', q('''SELECT openalex_id, display_name, canonical_id FROM researchers WHERE display_name LIKE '%Hiroaki Nakamura%' '''))
print('roster matched:', q('SELECT COUNT(*) FROM roster WHERE matched_researcher_id IS NOT NULL'))
print('kaken matched:', q('SELECT COUNT(*) FROM grant_members WHERE matched_researcher_id IS NOT NULL'))
print('metrics rows:', q('SELECT COUNT(*) FROM researcher_metrics'))
print('name_ja:', q('SELECT COUNT(*) FROM researchers WHERE name_ja IS NOT NULL'))
"
```

**誤マージ探知**（必須）: マージ済みクラスタに、rosterまたはKAKENの**異なる漢字氏名**が別エイリアスへ紐づいた形跡がないかを確認するクエリを実行し、0件であることを確認（0件でなければ該当クラスタを報告し、マージルールの見直しをBLOCKEDとして報告）:

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
# 各正準クラスタに対しKAKEN/rosterから来た漢字が2種類以上ないか
rows = c.execute('''
SELECT r.canonical_id, COUNT(DISTINCT gm.name_kanji)
FROM grant_members gm
JOIN researchers r ON r.openalex_id = gm.matched_researcher_id
WHERE r.canonical_id IS NOT NULL
GROUP BY r.canonical_id HAVING COUNT(DISTINCT gm.name_kanji) > 1
''').fetchall()
print('kanji-conflict clusters:', rows)
"
```

- [ ] **Step 3: 実表示確認（port 8199）**

```bash
uv run uvicorn web.app:create_default_app --factory --host 127.0.0.1 --port 8199 &
sleep 3
ALIAS=$(uv run python -c "import sqlite3; print(sqlite3.connect('db/researchers.db').execute('SELECT openalex_id FROM researchers WHERE canonical_id IS NOT NULL LIMIT 1').fetchone()[0])")
curl -s -o /dev/null -w "%{http_code} -> " "http://127.0.0.1:8199/researchers/${ALIAS}"
curl -s -o /dev/null -w "%{http_code}\n" -L "http://127.0.0.1:8199/researchers/${ALIAS}"
curl -s "http://127.0.0.1:8199/" | grep -oE "全[0-9,]+人中" | head -1
kill %1
```

Expected: `302 -> 200`、総数がエイリアス分減った正準数になる

- [ ] **Step 4: git確認**

```bash
git status --short   # 空（DBはgitignore）
```

---

## 完了条件

- 全テストPASS。実DBでエイリアス統合・誤マージ0件・roster/KAKEN matched増加・エイリアスURLの302を確認
