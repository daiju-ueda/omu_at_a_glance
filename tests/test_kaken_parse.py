import pytest
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
