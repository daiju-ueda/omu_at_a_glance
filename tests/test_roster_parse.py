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
