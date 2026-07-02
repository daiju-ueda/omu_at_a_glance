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
    assert client.calls[1][1]["p"] == 1


def _list_page(total, ids):
    cards = "\n".join(f"""
<div class="card-body result">
  <div class="name-kna">カ　メイ{i}</div>
  <div class="name-gng"><a href="/html/{i}_ja.html">名前 {i}</a></div>
</div>""" for i in ids)
    return f"<div>{total}件中</div>{cards}"


def test_sync_roster_paginates_with_p_param():
    # 実サイトの1ページ10件固定に合わせ、11件は2ページ目まで p=1,2 で取得する
    page1 = _list_page(11, range(1, 11))
    page2 = _list_page(11, [11])
    engine = get_engine(":memory:")
    client = FakeRosterClient([HOME, page1, page2])
    with Session(engine) as s:
        n = sync_roster(s, client, today=TODAY)
        assert n == 11
    assert client.calls[1][1]["p"] == 1
    assert client.calls[2][1]["p"] == 2
    assert "pp" not in client.calls[1][1]


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
