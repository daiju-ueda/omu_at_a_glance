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
    <li>
      <p class="title">共著論集</p>
      <p class="contents">山田太郎、鈴木花子</p>
      <p class="contents">吉川弘文館 2016年11月</p>
    </li>
  </ul>
</div>
<div id="knkyu_prsn">
  <ul>
    <li>
      <p class="title">御毛寺知識経と地域社会</p>
      <p class="contents">続日本紀研究会例会 2024年06月</p>
    </li>
    <li>
      <p class="title">地域史研究の展望国内会議</p>
      <p class="contents">続日本紀研究会例会 2024年06月</p>
    </li>
  </ul>
</div>
<div id="gkkai_iinkai">
  <ul>
    <li>
      <p class="">大阪公立大学日本史学会 委員 </p>
      <p class="contents"><span>2013年05月</span><span>-</span><span>継続中</span></p>
    </li>
    <li>
      <p class="">和泉市史編さん委員会 調査執筆委員</p>
      <p class="contents">年不明</p>
    </li>
    <li>
      <p class="">審議会・政策研究会等の委員会 環境審議会 委員</p>
      <p class="contents">2015年04月</p>
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
    # 1つ目のcontentsが著者一覧（年無し）でも、2つ目のcontentsから年を拾う
    assert by_cat["book"][1]["detail"] == "山田太郎、鈴木花子"
    assert by_cat["book"][1]["year"] == 2016
    assert by_cat["presentation"][0]["year"] == 2024
    # タイトルに直接くっついた「国内会議」「国際会議」バッジは除去する
    assert by_cat["presentation"][1]["title"] == "地域史研究の展望"
    assert by_cat["committee"][0]["title"] == "大阪公立大学日本史学会 委員"
    assert by_cat["committee"][0]["year"] == 2013
    assert by_cat["committee"][1]["year"] is None  # 年が無ければNone
    # 「審議会・政策研究会等の委員会」で始まるタイトルはスキップ対象
    assert all(not c["title"].startswith("審議会・政策研究会等の委員会")
               for c in by_cat["committee"])
    assert len(by_cat["committee"]) == 2
    assert len(by_cat["book"]) == 2
    assert len(by_cat["presentation"]) == 2
    assert len(entries) == 8


def test_parse_profile_achievements_missing_sections():
    assert parse_profile_achievements("<html><body>なにもない</body></html>") == []
