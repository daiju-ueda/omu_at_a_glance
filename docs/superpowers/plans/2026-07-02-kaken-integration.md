# KAKEN科研費統合 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KAKEN APIから大阪公立大学の科研費課題（種目・期間・配分額・代表/分担者）を収集し、カナ→ローマ字の名寄せでOpenAlex研究者に紐付けて、科研費メトリクス3種と日本語氏名を追加する。

**Architecture:** `collector/config.py`（.env読込）→ `collector/kaken.py`（クライアント＋XMLパーサ＋sync＋名寄せ）→ `collector/nameutil.py`（カナ→ローマ字変体）→ metrics/web拡張。**appid有効化待ちのためTask 1-5はfixture TDDで完結し、Task 6で実APIと照合する**（XMLの実形状が暫定fixtureと違えばTask 6でパーサとfixtureを実形状に更新する契約）。

**Tech Stack:** 既存スタック（httpx / SQLAlchemy）＋ `defusedxml==0.7.1`（XXE/billion-laughs対策のXMLパーサ。stdlib xml.etreeで直接パースしない）。

**設計書:** `docs/superpowers/specs/2026-07-02-kaken-integration-design.md`

## Global Constraints

- appidは `.env` の `KAKEN_APPID=...` からのみ読む。ハードコード・コミット・ログ出力禁止（`.gitignore` に `.env` 追加）
- KAKENクエリは `kw=大阪公立大学` ＋**クライアント側で institution 完全一致フィルタ**（専用パラメタの有無に依存しない設計。Task 6でより良いパラメタが見つかれば置換可、クライアント側フィルタは保険として残す）
- ウィンドウ: `end_fiscal_year >= window_start(今日)の年` かつ `start_fiscal_year <= 今日の年` の課題のみ保存
- grantsは全洗い替え。ただし**パース結果0件なら洗い替えせず警告**（全消し事故ガード）
- 名寄せは学内一意マッチのみ自動確定。カナ無しメンバーは対象外。一意マッチ研究者の `researchers.name_ja` にKAKEN漢字氏名を書き込む
- `kaken_total_amount` は**代表（principal）課題のみ**の合計。表示は万円（`{v//10000:,}万円`、0/Noneは「–」）
- 403（Invalid APPID）はリトライせずスキップ（他ステージ継続）。429/5xxは指数バックオフ（既存と同じ1,2,4,8,16秒・最大6回）
- スキーマ変更のDB反映は確立済みの運用（`rm db/researchers.db*` → full sync）で行う（Task 6）

---

### Task 1: .envローダとKAKEN HTTPクライアント

**Files:**
- Create: `collector/config.py`
- Create: `collector/kaken.py`（クライアント部分のみ。パーサ/syncはTask 3-4で同ファイルに追記）
- Modify: `.gitignore`（`.env` を追加）
- Test: `tests/test_config.py`, `tests/test_kaken_client.py`

**Interfaces:**
- Produces:
  - `collector.config.load_env(path=".env") -> dict[str, str]` — `KEY=VALUE` 行をパース（`#`始まり・空行・`=`無し行は無視、値の前後空白除去）。ファイル無しは空dict
  - `collector.config.get_kaken_appid() -> str | None` — `os.environ["KAKEN_APPID"]` 優先、無ければ `.env` から
  - `collector.kaken.KakenAuthError(Exception)`
  - `collector.kaken.KakenClient(appid: str, transport=None, sleep_fn=time.sleep)`
  - `.fetch(params: dict) -> str` — `https://kaken.nii.ac.jp/opensearch/` にGET、`appid` を必ず付与、XML文字列を返す。403は `KakenAuthError`、429/5xxは1,2,4,8,16秒バックオフ最大6回

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_config.py`）

```python
from collector.config import get_kaken_appid, load_env


def test_load_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nKAKEN_APPID = abc123 \n\nBROKEN_LINE\nX=1\n",
                 encoding="utf-8")
    env = load_env(str(p))
    assert env == {"KAKEN_APPID": "abc123", "X": "1"}


def test_load_env_missing_file(tmp_path):
    assert load_env(str(tmp_path / "nope.env")) == {}


def test_get_kaken_appid_prefers_environ(tmp_path, monkeypatch):
    p = tmp_path / ".env"
    p.write_text("KAKEN_APPID=from_file\n", encoding="utf-8")
    monkeypatch.setenv("KAKEN_APPID", "from_env")
    assert get_kaken_appid(str(p)) == "from_env"
    monkeypatch.delenv("KAKEN_APPID")
    assert get_kaken_appid(str(p)) == "from_file"
    assert get_kaken_appid(str(tmp_path / "nope.env")) is None
```

（`tests/test_kaken_client.py`）

```python
import httpx
import pytest

from collector.kaken import KakenAuthError, KakenClient

XML_OK = '<?xml version="1.0"?><grantAwards total="0"></grantAwards>'


def make_client(handler):
    return KakenClient("APPID_X", transport=httpx.MockTransport(handler),
                       sleep_fn=lambda s: None)


def test_fetch_sends_appid_and_returns_xml():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, text=XML_OK)

    client = make_client(handler)
    body = client.fetch({"kw": "大阪公立大学", "rw": 500, "st": 1})
    assert body == XML_OK
    assert seen["appid"] == "APPID_X"
    assert seen["kw"] == "大阪公立大学"


def test_403_raises_auth_error_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(403, text="Invalid APPID")

    client = make_client(handler)
    with pytest.raises(KakenAuthError):
        client.fetch({})
    assert calls["n"] == 1


def test_retries_on_503_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, text=XML_OK)

    client = make_client(handler)
    assert client.fetch({}) == XML_OK
    assert calls["n"] == 3
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_config.py tests/test_kaken_client.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装**

`.gitignore` に1行追加: `.env`

`collector/config.py`:

```python
import os


def load_env(path: str = ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def get_kaken_appid(path: str = ".env") -> str | None:
    return os.environ.get("KAKEN_APPID") or load_env(path).get("KAKEN_APPID")
```

`collector/kaken.py`:

```python
import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://kaken.nii.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6


class KakenAuthError(Exception):
    pass


class KakenClient:
    def __init__(self, appid: str, transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._appid = appid
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport)

    def fetch(self, params: dict) -> str:
        params = {**params, "appid": self._appid}
        for attempt in range(MAX_TRIES):
            try:
                resp = self._http.get("/opensearch/", params=params)
            except httpx.TransportError:
                if attempt == MAX_TRIES - 1:
                    raise
                self._sleep(2 ** attempt)
                continue
            if resp.status_code == 403:
                raise KakenAuthError("KAKEN appid rejected (403)")
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_TRIES - 1:
                    break
                wait = 2 ** attempt
                logger.warning("KAKEN -> %s, retry in %ss",
                               resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        resp.raise_for_status()
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_config.py tests/test_kaken_client.py -v` → PASS (6 tests)。`uv run pytest -m "not smoke"` 全件PASS

- [ ] **Step 5: Commit**

```bash
git add collector/config.py collector/kaken.py .gitignore tests/test_config.py tests/test_kaken_client.py
git commit -m "feat: .envローダとKAKEN APIクライアント（403は即スキップ）"
```

---

### Task 2: カナ→ローマ字変体（nameutil）

**Files:**
- Create: `collector/nameutil.py`
- Test: `tests/test_nameutil.py`

**Interfaces:**
- Produces:
  - `collector.nameutil.kana_part_variants(part: str) -> set[str]` — カタカナ/ひらがな1語（姓または名）のヘボン式ローマ字変体。長音表記ゆれ（ou→ou/o/oh、oo→oo/o/oh、uu→uu/u、ei→ei/e）を展開、変体は最大32でクリップ
  - `collector.nameutil.normalize_name(s: str) -> str` — 小文字化し `a-z0-9` と空白以外を除去、連続空白を1つに（マッチング用正規化。OpenAlex名とローマ字変体の両方に適用）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_nameutil.py`）

```python
from collector.nameutil import kana_part_variants, normalize_name


def test_basic():
    assert kana_part_variants("ヤマダ") == {"yamada"}
    assert kana_part_variants("すずき") == {"suzuki"}


def test_long_vowel_variants():
    assert kana_part_variants("タロウ") == {"tarou", "taro", "taroh"}
    assert kana_part_variants("ユウコ") == {"yuuko", "yuko"}
    assert kana_part_variants("オオノ") == {"oono", "ono", "ohno"}
    assert "yoko" in kana_part_variants("ヨウコ")


def test_ei_variants():
    assert kana_part_variants("ケイコ") == {"keiko", "keko"}


def test_digraph_sokuon_n():
    assert kana_part_variants("シュンスケ") == {"shunsuke"}
    assert kana_part_variants("ハットリ") == {"hattori"}
    assert "junichirou" in kana_part_variants("ジュンイチロウ")
    # ン + b/m/p → m 変体
    assert kana_part_variants("ホンマ") == {"honma", "homma"}


def test_chouon_mark():
    assert "ryusei" in {v.replace("ryuu", "ryu") for v in kana_part_variants("リュウセイ")} or "ryusei" in kana_part_variants("リュウセイ")


def test_normalize_name():
    assert normalize_name("Jun'ichiro  Tanaka-Sato") == "junichiro tanakasato"
    assert normalize_name("Daiju Ueda") == "daiju ueda"


def test_empty_and_unknown_chars():
    assert kana_part_variants("") == set()
    assert kana_part_variants("山田") == set()  # 漢字は変換不能→空
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_nameutil.py -v`
Expected: FAIL（import error）

- [ ] **Step 3: 実装**（`collector/nameutil.py`）

```python
import re

# ヘボン式（拗音含む）。キーはカタカナ
_DIGRAPHS = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo",
    "シャ": "sha", "シュ": "shu", "ショ": "sho",
    "チャ": "cha", "チュ": "chu", "チョ": "cho",
    "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo",
    "ヒャ": "hya", "ヒュ": "hyu", "ヒョ": "hyo",
    "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo",
    "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo",
    "ジャ": "ja", "ジュ": "ju", "ジョ": "jo",
    "ビャ": "bya", "ビュ": "byu", "ビョ": "byo",
    "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo",
    "ヂャ": "ja", "ヂュ": "ju", "ヂョ": "jo",
}
_MONO = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "o",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "ヴ": "vu",
}
_MAX_VARIANTS = 32


def _to_katakana(s: str) -> str:
    return "".join(
        chr(ord(ch) + 0x60) if "ぁ" <= ch <= "ゖ" else ch for ch in s)


def _base_romaji(kana: str) -> str | None:
    kana = _to_katakana(kana.strip())
    out = []
    i = 0
    sokuon = False
    while i < len(kana):
        two = kana[i:i + 2]
        ch = kana[i]
        if ch == "ッ":
            sokuon = True
            i += 1
            continue
        if ch == "ー":
            if out and out[-1] and out[-1][-1] in "aiueo":
                out.append(out[-1][-1])  # 直前母音を伸ばす
            i += 1
            continue
        if ch == "ン":
            out.append("n")
            i += 1
            continue
        if two in _DIGRAPHS:
            syl = _DIGRAPHS[two]
            i += 2
        elif ch in _MONO:
            syl = _MONO[ch]
            i += 1
        else:
            return None  # カナ以外（漢字等）は変換不能
        if sokuon:
            syl = syl[0] + syl
            sokuon = False
        out.append(syl)
    return "".join(out) if out else None


def kana_part_variants(part: str) -> set[str]:
    base = _base_romaji(part)
    if base is None:
        return set()
    variants = {base}
    # ン + b/m/p → m
    m_variant = re.sub(r"n(?=[bmp])", "m", base)
    variants.add(m_variant)
    # 長音の表記ゆれ展開
    expansions = [("ou", ("ou", "o", "oh")), ("oo", ("oo", "o", "oh")),
                  ("uu", ("uu", "u")), ("ei", ("ei", "e"))]
    for _ in range(3):  # 複数箇所に対応するため数回適用
        new = set()
        for v in variants:
            new.add(v)
            for pat, alts in expansions:
                if pat in v:
                    for alt in alts:
                        new.add(v.replace(pat, alt, 1))
        variants = new
        if len(variants) > _MAX_VARIANTS:
            break
    return set(list(variants)[:_MAX_VARIANTS])


def normalize_name(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r" +", " ", s).strip()
```

- [ ] **Step 4: テスト通過を確認**

Run: `uv run pytest tests/test_nameutil.py -v` → PASS。全suite PASS

- [ ] **Step 5: Commit**

```bash
git add collector/nameutil.py tests/test_nameutil.py
git commit -m "feat: カナ→ヘボン式ローマ字変体（名寄せ用）"
```

---

### Task 3: grantsモデル＋KAKEN XMLパーサ（暫定fixture）

**Files:**
- Modify: `pyproject.toml`（dependencies に `"defusedxml==0.7.1"` を追加し `uv sync`）
- Modify: `db/models.py`（Grant / GrantMember 追加、ResearcherMetrics に kaken 3列追加）
- Modify: `collector/kaken.py`（パーサ関数追記）
- Test: `tests/test_kaken_parse.py`

**Interfaces:**
- Produces:
  - `db.models.Grant(award_id: str [PK], title: str, category: str|None, start_year: int|None, end_year: int|None, total_amount: int (default 0), raw_json: str, updated_at: str)`
  - `db.models.GrantMember(award_id: str [PK], erad_id: str [PK], name_kanji: str, name_kana: str|None, role: str, matched_researcher_id: str|None)`
  - `ResearcherMetrics` 追加列: `kaken_pi_count: int (default 0)`, `kaken_copi_count: int (default 0)`, `kaken_total_amount: int (default 0)`
  - `collector.kaken.parse_grants(xml_text: str) -> tuple[list[tuple[dict, list[dict]]], int]` — `([(grant_kwargs, member_kwargs_list), ...], total件数)`。**この関数と fixture が「KAKEN XMLの形状に関する知識」の唯一の置き場**（Task 6で実XMLと照合し、違いがあればここだけ直す）
  - member_kwargs: `award_id`, `erad_id`（eradCode。空なら `"name:"+name_kanji`）, `name_kanji`, `name_kana`（無ければNone）, `role`（"principal_investigator" を含めば "principal"、それ以外は "co_investigator"）。matched_researcher_id はパース段階では設定しない
  - XMLはnamespace除去してからパース。1課題のパース失敗はスキップしてログ警告

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_kaken_parse.py`）

```python
from collector.kaken import parse_grants

# 暫定fixture: KAKEN公開XMLの想定形状。Task 6で実レスポンスと照合し、
# 違いがあればパーサとともにこのfixtureを実形状へ更新する
XML = """<?xml version="1.0" encoding="UTF-8"?>
<grantAwards total="2" start="1" pagesize="500">
  <grantAward awardNumber="22K07777">
    <summary xml:lang="ja">
      <title>深層学習による画像診断支援</title>
      <category>基盤研究(C)</category>
      <institution>大阪公立大学</institution>
      <periodOfAward>
        <startFiscalYear>2022</startFiscalYear>
        <endFiscalYear>2025</endFiscalYear>
      </periodOfAward>
      <member eradCode="40000001" role="principal_investigator">
        <personalName>
          <fullName>山田 太郎</fullName>
          <nameKana>ヤマダ タロウ</nameKana>
        </personalName>
      </member>
      <member eradCode="" role="co_investigator_buntan">
        <personalName>
          <fullName>鈴木 花子</fullName>
          <nameKana>スズキ ハナコ</nameKana>
        </personalName>
      </member>
      <overallAwardAmount>
        <totalCost>4550000</totalCost>
      </overallAwardAmount>
    </summary>
  </grantAward>
  <grantAward awardNumber="23H99999">
    <summary xml:lang="ja">
      <title>不完全データ課題</title>
      <institution>他大学</institution>
      <member role="principal_investigator">
        <personalName><fullName>田中 一郎</fullName></personalName>
      </member>
    </summary>
  </grantAward>
</grantAwards>
"""


def test_parse_grants():
    entries, total = parse_grants(XML)
    assert total == 2
    assert len(entries) == 2

    g1, members1 = entries[0]
    assert g1["award_id"] == "22K07777"
    assert g1["title"] == "深層学習による画像診断支援"
    assert g1["category"] == "基盤研究(C)"
    assert g1["institution"] == "大阪公立大学"
    assert g1["start_year"] == 2022 and g1["end_year"] == 2025
    assert g1["total_amount"] == 4550000
    assert len(members1) == 2
    assert members1[0] == {"award_id": "22K07777", "erad_id": "40000001",
                           "name_kanji": "山田 太郎", "name_kana": "ヤマダ タロウ",
                           "role": "principal"}
    assert members1[1]["erad_id"] == "name:鈴木 花子"
    assert members1[1]["role"] == "co_investigator"

    g2, members2 = entries[1]
    assert g2["institution"] == "他大学"
    assert g2["category"] is None
    assert g2["start_year"] is None and g2["total_amount"] == 0
    assert members2[0]["name_kana"] is None


def test_parse_grants_empty():
    entries, total = parse_grants(
        '<?xml version="1.0"?><grantAwards total="0"></grantAwards>')
    assert entries == [] and total == 0


def test_parse_grants_rejects_entity_expansion():
    # defusedxml採用の確認（XXE/billion-laughs対策）
    evil = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>'
            '<grantAwards total="0">&a;</grantAwards>')
    with pytest.raises(Exception):
        parse_grants(evil)
```

（ファイル冒頭に `import pytest` が必要）

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_kaken_parse.py -v` → FAIL（import error）

- [ ] **Step 3: 依存追加とモデル追加**

`pyproject.toml` の `dependencies` に `"defusedxml==0.7.1",` を追加し `uv sync` を実行。

`db/models.py` — `SyncState` クラスの後に追加:

```python
class Grant(Base):
    __tablename__ = "grants"
    award_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_amount: Mapped[int] = mapped_column(Integer, default=0)
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class GrantMember(Base):
    __tablename__ = "grant_members"
    award_id: Mapped[str] = mapped_column(String, primary_key=True)
    erad_id: Mapped[str] = mapped_column(String, primary_key=True)
    name_kanji: Mapped[str] = mapped_column(String)
    name_kana: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String)
    matched_researcher_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True)
```

`ResearcherMetrics` の `top_subfield` 行の直後に追加:

```python
    kaken_pi_count: Mapped[int] = mapped_column(Integer, default=0)
    kaken_copi_count: Mapped[int] = mapped_column(Integer, default=0)
    kaken_total_amount: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: パーサ実装**（`collector/kaken.py` に追記）

```python
import json
import re as _re
import xml.etree.ElementTree as ET

# XXE/billion-laughs対策: パースは必ずdefusedxml経由で行う
# （ET.tostringは既にパース済みのtreeの直列化なのでstdlibで安全）
from defusedxml.ElementTree import fromstring as _safe_fromstring


def _strip_namespaces(xml_text: str) -> str:
    return _re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", xml_text)


def _int_or_none(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_grants(xml_text: str) -> tuple[list[tuple[dict, list[dict]]], int]:
    root = _safe_fromstring(_strip_namespaces(xml_text))
    total = _int_or_none(root.get("total")) or 0
    entries = []
    for ga in root.iter("grantAward"):
        try:
            award_id = ga.get("awardNumber") or ""
            if not award_id:
                raise ValueError("awardNumber missing")
            summary = ga.find("summary")
            if summary is None:
                raise ValueError("summary missing")
            period = summary.find("periodOfAward")
            grant = {
                "award_id": award_id,
                "title": (summary.findtext("title") or "").strip(),
                "category": summary.findtext("category"),
                "institution": (summary.findtext("institution") or "").strip(),
                "start_year": _int_or_none(
                    period.findtext("startFiscalYear")) if period is not None else None,
                "end_year": _int_or_none(
                    period.findtext("endFiscalYear")) if period is not None else None,
                "total_amount": _int_or_none(
                    summary.findtext("overallAwardAmount/totalCost")) or 0,
                "raw_json": json.dumps(
                    ET.tostring(ga, encoding="unicode"), ensure_ascii=False),
            }
            members = []
            for m in summary.iter("member"):
                name_kanji = (m.findtext("personalName/fullName") or "").strip()
                if not name_kanji:
                    continue
                erad = (m.get("eradCode") or "").strip()
                kana = m.findtext("personalName/nameKana")
                role = m.get("role") or ""
                members.append({
                    "award_id": award_id,
                    "erad_id": erad or f"name:{name_kanji}",
                    "name_kanji": name_kanji,
                    "name_kana": kana.strip() if kana else None,
                    "role": ("principal" if "principal_investigator" in role
                             else "co_investigator"),
                })
            entries.append((grant, members))
        except (ValueError, AttributeError) as e:
            logger.warning("grantAwardのパースをスキップ: %s", e)
    return entries, total
```

注: `grant` dictには一時キー `institution` を含む（DB列には無い。sync側のフィルタ用で、upsert前に除去する — Task 4参照）。

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_kaken_parse.py -v` → PASS。全suite PASS

- [ ] **Step 6: Commit**

```bash
git add db/models.py collector/kaken.py tests/test_kaken_parse.py
git commit -m "feat: grants/grant_membersモデルとKAKEN XMLパーサ（暫定fixture）"
```

---

### Task 4: KAKEN同期＋名寄せ＋メトリクス集計

**Files:**
- Modify: `collector/kaken.py`（sync_kaken / match_members 追記）
- Modify: `collector/metrics.py`（kaken集計追加）
- Test: `tests/test_kaken_sync.py`, `tests/test_metrics.py`（kakenアサーション追記）

**Interfaces:**
- Consumes: Task 1-3の全て、`collector.sync.window_start`、`collector.nameutil`
- Produces:
  - `collector.kaken.sync_kaken(session, client, today: datetime.date, institution: str = "大阪公立大学") -> int` — `kw=institution` で全ページ取得（rw=500, st=1,501,...）→ institution完全一致＋年度ウィンドウでフィルタ→grants/grant_members全洗い替え（0件なら警告して保持）→保存件数を返す。`sync_state`(source="kaken")更新
  - `collector.kaken.match_members(session) -> int` — 全grant_membersの名寄せを再計算し、一意マッチ数を返す。一意マッチの `researchers.name_ja` にname_kanjiを書き込む
  - `compute_metrics` が kaken_pi_count / kaken_copi_count / kaken_total_amount を集計（grants空なら全て0）

- [ ] **Step 1: 失敗するテストを書く**（`tests/test_kaken_sync.py`）

```python
import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.kaken import match_members, sync_kaken
from db.models import Grant, GrantMember, Researcher, SyncState, get_engine

TODAY = datetime.date(2026, 7, 2)  # window_start = 2023-07-02 → 2023年度以降が対象

XML_PAGE = """<?xml version="1.0"?>
<grantAwards total="3" start="1" pagesize="500">
  <grantAward awardNumber="22K01111">
    <summary xml:lang="ja">
      <title>対象課題</title>
      <category>基盤研究(B)</category>
      <institution>大阪公立大学</institution>
      <periodOfAward><startFiscalYear>2022</startFiscalYear><endFiscalYear>2025</endFiscalYear></periodOfAward>
      <member eradCode="E1" role="principal_investigator">
        <personalName><fullName>山田 太郎</fullName><nameKana>ヤマダ タロウ</nameKana></personalName>
      </member>
      <overallAwardAmount><totalCost>10000000</totalCost></overallAwardAmount>
    </summary>
  </grantAward>
  <grantAward awardNumber="18K02222">
    <summary xml:lang="ja">
      <title>ウィンドウ外（2020年度終了）</title>
      <institution>大阪公立大学</institution>
      <periodOfAward><startFiscalYear>2018</startFiscalYear><endFiscalYear>2020</endFiscalYear></periodOfAward>
      <member eradCode="E1" role="principal_investigator">
        <personalName><fullName>山田 太郎</fullName><nameKana>ヤマダ タロウ</nameKana></personalName>
      </member>
    </summary>
  </grantAward>
  <grantAward awardNumber="23K03333">
    <summary xml:lang="ja">
      <title>他機関の課題（kwにヒットしただけ）</title>
      <institution>大阪大学</institution>
      <periodOfAward><startFiscalYear>2023</startFiscalYear><endFiscalYear>2026</endFiscalYear></periodOfAward>
      <member eradCode="E9" role="principal_investigator">
        <personalName><fullName>大阪 公立太</fullName><nameKana>オオサカ コウリツタ</nameKana></personalName>
      </member>
    </summary>
  </grantAward>
</grantAwards>
"""


class FakeKakenClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def fetch(self, params):
        self.calls.append(dict(params))
        return self.pages[len(self.calls) - 1]


def _researcher(id_, name):
    return Researcher(openalex_id=id_, display_name=name, h_index=1,
                      works_count=1, raw_json="{}", updated_at="")


def test_sync_kaken_filters_and_stores():
    engine = get_engine(":memory:")
    client = FakeKakenClient([XML_PAGE])
    with Session(engine) as s:
        n = sync_kaken(s, client, today=TODAY)
        assert n == 1  # 対象課題のみ（ウィンドウ外と他機関は除外）
        g = s.get(Grant, "22K01111")
        assert g.total_amount == 10000000
        assert s.get(Grant, "18K02222") is None
        assert s.get(Grant, "23K03333") is None
        assert s.get(GrantMember, ("22K01111", "E1")).role == "principal"
        assert s.get(SyncState, "kaken").last_synced_at == "2026-07-02"
    assert client.calls[0]["kw"] == "大阪公立大学"
    assert client.calls[0]["st"] == 1


def test_sync_kaken_empty_keeps_existing(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Grant(award_id="OLD1", title="既存", total_amount=1,
                    raw_json="{}", updated_at=""))
        s.commit()
        client = FakeKakenClient(
            ['<?xml version="1.0"?><grantAwards total="0"></grantAwards>'])
        with caplog.at_level("WARNING"):
            n = sync_kaken(s, client, today=TODAY)
        assert n == 0
        assert s.get(Grant, "OLD1") is not None  # 全消し事故ガード
    assert any("skip" in r.message.lower() or "スキップ" in r.message
               for r in caplog.records)


def test_match_members_unique_and_ambiguous():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add_all([
            _researcher("A1", "Taro Yamada"),
            _researcher("A2", "Hanako Suzuki"),
            _researcher("A3", "Hanako Suzuki"),   # 同名 → 曖昧
        ])
        s.add(Grant(award_id="G1", title="t", total_amount=0,
                    raw_json="{}", updated_at=""))
        s.add_all([
            GrantMember(award_id="G1", erad_id="E1", name_kanji="山田 太郎",
                        name_kana="ヤマダ タロウ", role="principal"),
            GrantMember(award_id="G1", erad_id="E2", name_kanji="鈴木 花子",
                        name_kana="スズキ ハナコ", role="co_investigator"),
            GrantMember(award_id="G1", erad_id="E3", name_kanji="無 名",
                        name_kana=None, role="co_investigator"),
        ])
        s.commit()

        n = match_members(s)
        assert n == 1  # 一意マッチは山田のみ
        assert s.get(GrantMember, ("G1", "E1")).matched_researcher_id == "A1"
        assert s.get(GrantMember, ("G1", "E2")).matched_researcher_id is None
        assert s.get(GrantMember, ("G1", "E3")).matched_researcher_id is None
        assert s.get(Researcher, "A1").name_ja == "山田 太郎"
        assert s.get(Researcher, "A2").name_ja is None
```

`tests/test_metrics.py` の `test_compute_metrics` に追記（seed部の `s.commit()` の直前にkaken seedを追加し、アサーション末尾に検証を追加）:

seed追加:

```python
        s.add_all([
            Grant(award_id="G1", title="t", total_amount=10_000_000,
                  raw_json="{}", updated_at=""),
            Grant(award_id="G2", title="t", total_amount=3_000_000,
                  raw_json="{}", updated_at=""),
        ])
        s.add_all([
            GrantMember(award_id="G1", erad_id="E1", name_kanji="x",
                        name_kana=None, role="principal",
                        matched_researcher_id="A1"),
            GrantMember(award_id="G2", erad_id="E2", name_kanji="x",
                        name_kana=None, role="co_investigator",
                        matched_researcher_id="A1"),
            GrantMember(award_id="G2", erad_id="E3", name_kanji="y",
                        name_kana=None, role="principal",
                        matched_researcher_id=None),  # 未マッチは集計外
        ])
```

（importに `Grant, GrantMember` を追加）

アサーション追加（m1ブロック内）:

```python
        assert m1.kaken_pi_count == 1
        assert m1.kaken_copi_count == 1
        assert m1.kaken_total_amount == 10_000_000  # 代表課題のみ
```

（m2ブロック内）:

```python
        assert m2.kaken_pi_count == 0 and m2.kaken_total_amount == 0
```

- [ ] **Step 2: 失敗を確認**

Run: `uv run pytest tests/test_kaken_sync.py tests/test_metrics.py -v` → FAIL

- [ ] **Step 3: sync_kaken / match_members 実装**（`collector/kaken.py` に追記）

```python
import datetime
from collections import defaultdict

from sqlalchemy import delete, select

from collector.nameutil import kana_part_variants, normalize_name
from collector.sync import window_start
from db.models import Grant, GrantMember, Researcher, SyncState

PAGE_SIZE_KAKEN = 500


def sync_kaken(session, client, today: datetime.date,
               institution: str = "大阪公立大学") -> int:
    window_year = int(window_start(today)[:4])
    current_year = today.year
    entries: list[tuple[dict, list[dict]]] = []
    st = 1
    while True:
        xml_text = client.fetch({"kw": institution,
                                 "rw": PAGE_SIZE_KAKEN, "st": st})
        page_entries, total = parse_grants(xml_text)
        entries.extend(page_entries)
        st += PAGE_SIZE_KAKEN
        if st > total:
            break

    kept = []
    for grant, members in entries:
        if grant.pop("institution", "") != institution:
            continue
        end = grant["end_year"]
        start = grant["start_year"]
        if end is not None and end < window_year:
            continue
        if start is not None and start > current_year:
            continue
        kept.append((grant, members))

    if not kept:
        logger.warning("KAKEN: 0件のため洗い替えをスキップ（既存データ保持）")
        return 0

    session.execute(delete(GrantMember))
    session.execute(delete(Grant))
    for grant, members in kept:
        session.add(Grant(**grant, updated_at=today.isoformat()))
        seen_member_keys = set()
        for m in members:
            key = (m["award_id"], m["erad_id"])
            if key in seen_member_keys:
                continue
            seen_member_keys.add(key)
            session.add(GrantMember(**m))
    session.merge(SyncState(source="kaken", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("KAKEN sync done: %d grants", len(kept))
    return len(kept)


def match_members(session) -> int:
    index: dict[str, set[str]] = defaultdict(set)
    for rid, name in session.execute(
            select(Researcher.openalex_id, Researcher.display_name)):
        index[normalize_name(name)].add(rid)

    matched = 0
    name_ja_by_rid: dict[str, str] = {}
    for member in session.scalars(select(GrantMember)):
        member.matched_researcher_id = None
        if not member.name_kana:
            continue
        parts = member.name_kana.replace("　", " ").split()
        if len(parts) != 2:
            continue
        family_variants = kana_part_variants(parts[0])
        given_variants = kana_part_variants(parts[1])
        candidates: set[str] = set()
        for fam in family_variants:
            for giv in given_variants:
                candidates |= index.get(normalize_name(f"{giv} {fam}"), set())
                candidates |= index.get(normalize_name(f"{fam} {giv}"), set())
        if len(candidates) == 1:
            rid = candidates.pop()
            member.matched_researcher_id = rid
            name_ja_by_rid[rid] = member.name_kanji.replace("　", " ")
            matched += 1

    for rid, name_ja in name_ja_by_rid.items():
        researcher = session.get(Researcher, rid)
        if researcher is not None:
            researcher.name_ja = name_ja
    session.commit()
    logger.info("KAKEN名寄せ: %d人を一意マッチ", matched)
    return matched
```

- [ ] **Step 4: metrics拡張**（`collector/metrics.py`）

importに `Grant, GrantMember` を追加し、`compute_metrics` の `by_author` 構築の直後に追加:

```python
    kaken_by_author: dict[str, list] = {}
    for row in session.execute(
        select(GrantMember.matched_researcher_id, GrantMember.role,
               Grant.total_amount)
        .join(Grant, Grant.award_id == GrantMember.award_id)
        .where(GrantMember.matched_researcher_id.is_not(None))
    ):
        kaken_by_author.setdefault(row.matched_researcher_id, []).append(row)
```

`ResearcherMetrics(...)` の `top_subfield=top_subfield,` の直後に追加:

```python
            kaken_pi_count=sum(
                1 for k in kaken_by_author.get(rid, [])
                if k.role == "principal"),
            kaken_copi_count=sum(
                1 for k in kaken_by_author.get(rid, [])
                if k.role == "co_investigator"),
            kaken_total_amount=sum(
                k.total_amount for k in kaken_by_author.get(rid, [])
                if k.role == "principal"),
```

（grantsはsync時点でウィンドウフィルタ済みのため、metrics側での期間フィルタは不要）

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest tests/test_kaken_sync.py tests/test_metrics.py -v` → PASS。全suite PASS・pristine

- [ ] **Step 6: Commit**

```bash
git add collector/kaken.py collector/metrics.py tests/test_kaken_sync.py tests/test_metrics.py
git commit -m "feat: KAKEN同期・カナ名寄せ・科研費メトリクス集計"
```

---

### Task 5: sync CLI組込み＋Web表示

**Files:**
- Modify: `scripts/sync.py`（kakenステージ追加）
- Modify: `web/queries.py`（SORT_COLUMNSに追加）
- Modify: `web/app.py`（`man` フィルタ）
- Modify: `web/templates/ranking.html`, `web/templates/researcher.html`, `web/templates/base.html`
- Modify: `tests/conftest.py`（metricsにkaken値をseed）
- Test: `tests/test_web.py`, `tests/test_web_queries.py`
- Modify: `README.md`（.env設定手順）

**Interfaces:**
- Consumes: Task 1-4の全て
- Produces: `scripts/sync.py` がauthors→works→kaken（appid無しはスキップ）→metricsを実行。`/` に科研費総額ソート列、詳細カードに科研費3項目

- [ ] **Step 1: conftest seed拡張**（`tests/conftest.py`）

ResearcherMetrics A1 に `kaken_pi_count=2, kaken_copi_count=1, kaken_total_amount=75_000_000,`（`top_subfield="Health Informatics",` の直後）、A2 に `kaken_pi_count=0, kaken_copi_count=2, kaken_total_amount=0,`、A3 はそのまま（default 0）。

- [ ] **Step 2: 失敗するテストを書く**

`tests/test_web_queries.py` の `test_ranking_all_sort_keys` parametrize に追加:

```python
    ("kaken_total_amount", "A1"),  # 75,000,000
```

`tests/test_web.py` に追加:

```python
def test_ranking_kaken_column_and_sort(client):
    body = client.get("/?sort=kaken_total_amount&min_works=0").text
    assert "科研費総額" in body
    assert "7,500万円" in body
    assert body.index("Taro Yamada") < body.index("Hanako Suzuki")


def test_researcher_detail_kaken_card(client):
    body = client.get("/researchers/A1").text
    assert "科研費（代表）" in body and "科研費（分担）" in body
    assert "7,500万円" in body
    body3 = client.get("/researchers/A3").text
    assert "科研費（代表）" in body3  # 0件でもカードは出る（金額は–）
```

- [ ] **Step 3: 失敗を確認**

Run: `uv run pytest tests/test_web.py tests/test_web_queries.py -v` → FAIL

- [ ] **Step 4: 実装**

`web/queries.py` `SORT_COLUMNS` に追加:

```python
    "kaken_total_amount": ResearcherMetrics.kaken_total_amount,
```

`web/app.py`: `_pct` の直後に追加し、`create_app` 内でフィルタ登録:

```python
def _man(value):
    return "–" if not value else f"{value // 10000:,}万円"
```

```python
    templates.env.filters["man"] = _man
```

`web/templates/ranking.html`: theadの `責任` th の直後に追加:

```html
  <th><a href="/?sort=kaken_total_amount&min_works={{ min_works }}">科研費総額{% if sort == 'kaken_total_amount' %} ▼{% endif %}</a></th>
```

tbodyの `{{ m.corresponding_count }}` td の直後に追加:

```html
  <td>{{ m.kaken_total_amount|man }}</td>
```

`web/templates/researcher.html`: `{% if m %}` ブロック内、`<div><dt>主分野</dt>...` の直後に追加:

```html
  <div><dt>科研費（代表）</dt><dd>{{ m.kaken_pi_count }}</dd></div>
  <div><dt>科研費（分担）</dt><dd>{{ m.kaken_copi_count }}</dd></div>
  <div><dt>科研費配分総額</dt><dd>{{ m.kaken_total_amount|man }}</dd></div>
```

`web/templates/base.html`: フッター注記の末尾（`著者数補正の対象外。` の後）に追加:

```
科研費はKAKEN収録分・配分額は課題総額（代表課題のみ合算・按分なし）。
```

`scripts/sync.py`: importに以下を追加:

```python
from collector.config import get_kaken_appid
from collector.kaken import KakenAuthError, KakenClient, match_members, sync_kaken
```

`main()` の `n_w = sync_works(...)` の直後に追加:

```python
        appid = get_kaken_appid()
        if appid:
            try:
                n_k = sync_kaken(session, KakenClient(appid), today=today)
                n_match = match_members(session)
                logger.info("kaken: grants=%d matched=%d", n_k, n_match)
            except KakenAuthError:
                logger.warning("KAKEN appidが無効のためスキップ（有効化を待って再実行）")
        else:
            logger.warning("KAKEN_APPID未設定のためKAKEN同期をスキップ")
```

`README.md`: セットアップの直後に追加:

```markdown
## 環境変数

KAKEN同期にはCiNii/KAKENのappidが必要（未設定なら警告してスキップ）:

    echo "KAKEN_APPID=<発行されたappid>" > .env

`.env` はgitignore対象。コミットしないこと。
```

- [ ] **Step 5: テスト通過を確認**

Run: `uv run pytest -m "not smoke"` → 全件PASS・pristine

- [ ] **Step 6: Commit**

```bash
git add scripts/sync.py web tests/conftest.py tests/test_web.py tests/test_web_queries.py README.md
git commit -m "feat: 科研費のsync組込み・ランキング列・詳細カード・注記"
```

---

### Task 6: 実API照合＋DB再構築＋初回KAKEN同期（appid有効化後）

**Files:**
- Modify（必要時のみ）: `collector/kaken.py`, `tests/test_kaken_parse.py`, `tests/test_kaken_sync.py`（実XMLとの差分反映）
- 実行: `.env` 作成、DB再構築、full sync、検証

**Interfaces:**
- Consumes: Task 1-5の全て
- Produces: 実データ入りの grants / grant_members / kakenメトリクス / name_ja

- [ ] **Step 1: appid有効性確認**

```bash
curl -s "https://kaken.nii.ac.jp/opensearch/?appid=$(grep KAKEN_APPID .env | cut -d= -f2)&kw=cancer&rw=1" | head -c 300
```

まだ `Invalid APPID` なら**このタスクはBLOCKED**（残りのステップは実行不可。時間をおいて再試行）。

- [ ] **Step 2: .env 作成（未作成の場合）**

```bash
echo "KAKEN_APPID=<発行されたappid>" > .env
```

- [ ] **Step 3: 実XMLの形状照合**

```bash
APPID=$(grep KAKEN_APPID .env | cut -d= -f2)
curl -s "https://kaken.nii.ac.jp/opensearch/?appid=${APPID}&kw=%E5%A4%A7%E9%98%AA%E5%85%AC%E7%AB%8B%E5%A4%A7%E5%AD%A6&rw=2" > kaken_sample.xml
head -c 4000 kaken_sample.xml
```

実XMLの要素名・属性名・構造を `tests/test_kaken_parse.py` の暫定fixtureと比較する。**違いがあれば `parse_grants` とfixture・関連テストを実形状に合わせて修正**（何をどう変えたか報告に明記）。総件数(total)・institution表現・金額要素・カナの有無を必ず確認。件数regexやページングパラメタ（rw/st）が想定と違う場合も同様に修正。`kaken_sample.xml` はコミットしない（作業後削除）。

- [ ] **Step 4: 全テスト**

Run: `uv run pytest -m "not smoke"` → 全件PASS

- [ ] **Step 5: DB再構築＋初回同期（実API・数分）**

```bash
rm -f db/researchers.db db/researchers.db-wal db/researchers.db-shm
uv run python scripts/sync.py
```

Expected: `done: authors=~3876 works=~6200 metrics=~3876` に加えて `kaken: grants=<数百〜数千> matched=<数百以上>`、警告なし（KAKEN 0件警告が出たらパラメタ/フィルタを見直し）

- [ ] **Step 6: スポットチェック**

```bash
uv run python -c "
import sqlite3
c = sqlite3.connect('db/researchers.db')
q = lambda sql: c.execute(sql).fetchall()
print('grants:', q('SELECT COUNT(*) FROM grants'))
print('members:', q('SELECT COUNT(*), SUM(matched_researcher_id IS NOT NULL) FROM grant_members'))
print('name_ja set:', q('SELECT COUNT(*) FROM researchers WHERE name_ja IS NOT NULL'))
print('metrics kaken>0:', q('SELECT COUNT(*) FROM researcher_metrics WHERE kaken_pi_count > 0'))
print('sample:', q('SELECT r.display_name, r.name_ja, m.kaken_pi_count, m.kaken_total_amount FROM researcher_metrics m JOIN researchers r ON r.openalex_id=m.researcher_id WHERE m.kaken_total_amount > 0 ORDER BY m.kaken_total_amount DESC LIMIT 5'))
"
```

Expected: grants数百件以上、マッチ率の妥当性（members中の一意マッチが数百人規模）、name_ja が付いた研究者が数百人規模、sampleに漢字氏名と金額

- [ ] **Step 7: 変更があればCommit**

```bash
git add -A ':!kaken_sample.xml'
git status --short   # .env と db/* が出ないこと（gitignore）を確認
git commit -m "fix: KAKEN実XMLに合わせてパーサとfixtureを更新" # 変更があった場合のみ
```

---

## 完了条件

- `uv run pytest -m "not smoke"` 全件PASS
- 実DBに grants / grant_members / kakenメトリクスが入り、数百人規模の研究者に日本語氏名（name_ja）が付いている
- `/` の科研費総額ソートが動き、詳細カードに科研費3項目が出る
- `.env` と DBファイルがgitに入っていない
