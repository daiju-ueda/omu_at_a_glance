import pytest
from collector.kaken import parse_grants

# Task 6で実APIレスポンス（kaken.nii.ac.jp/opensearch/?format=xml&qe=...）と照合した実形状。
# 暫定fixtureとの主な差分:
# - 総件数は<grantAwards total="...">属性ではなく<totalResults>子要素
# - <summary>はxml:lang="ja"とxml:lang="en"の2つが並ぶ（ja側を明示的に選ぶ必要あり）
# - periodOfAwardの開始/終了年度は子要素ではなくsearchStartFiscalYear/searchEndFiscalYear属性
# - カナは<nameKana>ではなく<familyName yomi="...">/<givenName yomi="...">属性
XML = """<?xml version="1.0" encoding="UTF-8"?>
<grantAwards>
<totalResults>2</totalResults>
<startIndex>1</startIndex>
<itemsPerPage>500</itemsPerPage>
  <grantAward id="KAKENHI-PROJECT-22K07777" recordSet="kakenhi" projectType="project" awardNumber="22K07777">
    <summary xml:lang="ja">
      <title>深層学習による画像診断支援</title>
      <category path="000010" niiCode="10">基盤研究(C)</category>
      <institution niiCode="383195" sequence="1">大阪公立大学</institution>
      <periodOfAward searchStartFiscalYear="2022" searchEndFiscalYear="2025">
        <startDate>2022-04-01</startDate>
        <endDate estimated="false" nondisclosure="false">2026-03-31</endDate>
      </periodOfAward>
      <member sequence="1" eradCode="40000001" role="principal_investigator">
        <personalName sequence="1">
          <fullName>山田 太郎</fullName>
          <familyName yomi="ヤマダ">山田</familyName>
          <givenName yomi="タロウ">太郎</givenName>
        </personalName>
      </member>
      <member sequence="2" role="co_investigator_buntan">
        <personalName sequence="1">
          <fullName>鈴木 花子</fullName>
          <familyName yomi="スズキ">鈴木</familyName>
          <givenName yomi="ハナコ">花子</givenName>
        </personalName>
      </member>
      <overallAwardAmount planned="false" sequence="1" caption="配分額">
        <directCost>3500000</directCost>
        <indirectCost>1050000</indirectCost>
        <totalCost>4550000</totalCost>
      </overallAwardAmount>
    </summary>
    <summary xml:lang="en">
      <title>Deep learning assisted diagnostic imaging</title>
      <category path="000010" niiCode="10">Grant-in-Aid for Scientific Research (C)</category>
      <institution niiCode="383195" sequence="1">Osaka Metropolitan University</institution>
      <overallAwardAmount planned="false" sequence="1" caption="Budget Amount">
        <directCost>3500000</directCost>
        <indirectCost>1050000</indirectCost>
        <totalCost>4550000</totalCost>
      </overallAwardAmount>
    </summary>
  </grantAward>
  <grantAward id="KAKENHI-PROJECT-23H99999" recordSet="kakenhi" projectType="project" awardNumber="23H99999">
    <summary xml:lang="ja">
      <title>不完全データ課題</title>
      <institution niiCode="99999" sequence="1">他大学</institution>
      <member sequence="1" role="principal_investigator">
        <personalName sequence="1"><fullName>田中 一郎</fullName></personalName>
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
        '<?xml version="1.0"?><grantAwards><totalResults>0</totalResults></grantAwards>')
    assert entries == [] and total == 0


def test_parse_grants_rejects_entity_expansion():
    # defusedxml採用の確認（XXE/billion-laughs対策）
    evil = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>'
            '<grantAwards><totalResults>0</totalResults>&a;</grantAwards>')
    with pytest.raises(Exception):
        parse_grants(evil)


def test_parse_grants_skips_broken_entry_with_warning(caplog):
    xml = """<?xml version="1.0"?>
<grantAwards>
<totalResults>2</totalResults>
  <grantAward>
    <summary xml:lang="ja"><title>awardNumber無し</title></summary>
  </grantAward>
  <grantAward awardNumber="24K00001">
    <summary xml:lang="ja">
      <title>正常な課題</title>
      <institution niiCode="383195" sequence="1">大阪公立大学</institution>
    </summary>
  </grantAward>
</grantAwards>
"""
    with caplog.at_level("WARNING"):
        entries, total = parse_grants(xml)
    assert total == 2
    assert len(entries) == 1  # 壊れた1件はスキップ、残りは生きる
    assert entries[0][0]["award_id"] == "24K00001"
    assert any("スキップ" in r.message for r in caplog.records)
