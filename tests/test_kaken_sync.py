import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.kaken import match_members, sync_kaken
from db.models import Grant, GrantMember, Researcher, SyncState, get_engine

TODAY = datetime.date(2026, 7, 2)  # window_start = 2023-07-02 → 2023年度以降が対象

XML_PAGE = """<?xml version="1.0"?>
<grantAwards>
<totalResults>3</totalResults>
<startIndex>1</startIndex>
<itemsPerPage>500</itemsPerPage>
  <grantAward awardNumber="22K01111">
    <summary xml:lang="ja">
      <title>対象課題</title>
      <category>基盤研究(B)</category>
      <institution niiCode="383195" sequence="1">大阪公立大学</institution>
      <periodOfAward searchStartFiscalYear="2022" searchEndFiscalYear="2025"/>
      <member eradCode="E1" role="principal_investigator">
        <personalName><fullName>山田 太郎</fullName><familyName yomi="ヤマダ">山田</familyName><givenName yomi="タロウ">太郎</givenName></personalName>
      </member>
      <overallAwardAmount><totalCost>10000000</totalCost></overallAwardAmount>
    </summary>
  </grantAward>
  <grantAward awardNumber="18K02222">
    <summary xml:lang="ja">
      <title>ウィンドウ外（2020年度終了）</title>
      <institution niiCode="383195" sequence="1">大阪公立大学</institution>
      <periodOfAward searchStartFiscalYear="2018" searchEndFiscalYear="2020"/>
      <member eradCode="E1" role="principal_investigator">
        <personalName><fullName>山田 太郎</fullName><familyName yomi="ヤマダ">山田</familyName><givenName yomi="タロウ">太郎</givenName></personalName>
      </member>
    </summary>
  </grantAward>
  <grantAward awardNumber="23K03333">
    <summary xml:lang="ja">
      <title>他機関の課題（qeにヒットしただけ）</title>
      <institution niiCode="12345" sequence="1">大阪大学</institution>
      <periodOfAward searchStartFiscalYear="2023" searchEndFiscalYear="2026"/>
      <member eradCode="E9" role="principal_investigator">
        <personalName><fullName>大阪 公立太</fullName><familyName yomi="オオサカ">大阪</familyName><givenName yomi="コウリツタ">公立太</givenName></personalName>
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
    assert client.calls[0]["qe"] == "大阪公立大学"
    assert client.calls[0]["st"] == 1


def test_sync_kaken_empty_keeps_existing(caplog):
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Grant(award_id="OLD1", title="既存", total_amount=1,
                    raw_json="{}", updated_at=""))
        s.commit()
        client = FakeKakenClient(
            ['<?xml version="1.0"?><grantAwards><totalResults>0</totalResults></grantAwards>'])
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
