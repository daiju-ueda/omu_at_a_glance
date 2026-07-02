import pytest
from fastapi.testclient import TestClient

from web.app import create_app


@pytest.fixture()
def client(seeded_db_path):
    return TestClient(create_app(seeded_db_path))


def test_ranking_page_default(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Ichiro Tanaka" in body  # 既定min_works=1で全員表示
    assert body.index("山田 太郎") < body.index("Ichiro Tanaka")   # fwci_total 35.0 > 19.8
    assert body.index("Ichiro Tanaka") < body.index("Hanako Suzuki") # 19.8 > 0
    assert "全3人中 3人を表示" in body
    assert "最終同期: 2026-07-02" in body
    assert "OpenAlex収録分に基づく" in body
    assert "重複計上される場合がある" in body


def test_ranking_sort_and_min_works(client):
    body = client.get("/?sort=total_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("山田 太郎")
    body5 = client.get("/?min_works=5").text
    assert "全3人中 2人を表示" in body5
    assert "Ichiro Tanaka" not in body5


def test_ranking_fractional_sort_and_top1_column(client):
    body = client.get("/?sort=fractional_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("山田 太郎")  # 300>120.5
    assert "top1%" in body


def test_researcher_detail_new_metrics(client):
    body = client.get("/researchers/A1").text
    assert "国際共著率" in body and "50%" in body
    assert "産学連携率" in body and "10%" in body
    assert "Health Informatics" in body
    assert "ユニーク共著者" in body and "42" in body
    assert "i10指数" in body and "150" in body
    assert "120.50" in body  # 被引用(補正)


def test_ranking_invalid_params_fall_back(client):
    resp = client.get("/?sort=bogus&min_works=abc&page=-5")
    assert resp.status_code == 200
    assert "山田 太郎" in resp.text

    resp = client.get("/?page=99999999999999999999&min_works=99999999999999999999")
    assert resp.status_code == 200
    assert "山田 太郎" in resp.text


def test_researcher_detail(client):
    body = client.get("/researchers/A1").text
    assert "山田 太郎" in body
    assert "Deep Learning in Radiology" in body
    assert "Old Paper Outside Window" not in body  # ウィンドウ外
    assert "orcid.org/0000-0001-1111-1111" in body
    assert "top1%" in body
    assert "–" in body  # W3のFWCI欠損表示


def test_researcher_404(client):
    resp = client.get("/researchers/NOPE")
    assert resp.status_code == 404


def test_search(client):
    assert "山田 太郎" in client.get("/search?q=yama").text
    assert "見つかりませんでした" in client.get("/search?q=zzzzz").text
    assert client.get("/search").status_code == 200


def test_missing_db_fails_fast(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path / "missing.db"))


def test_no_api_docs(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_researcher_without_metrics_renders(client):
    resp = client.get("/researchers/A4")
    assert resp.status_code == 200
    assert "佐藤次郎" in resp.text
    assert "メトリクス未計算" in resp.text
    assert "i10指数" in resp.text


def test_pct_zero_renders_as_zero_percent(client):
    body = client.get("/researchers/A3").text
    assert "0%" in body  # corp_collab_rate=0.0は「–」でなく0%


def test_ranking_kaken_column_and_sort(client):
    body = client.get("/?sort=kaken_total_amount&min_works=0").text
    assert "科研費総額" in body
    assert "7,500万円" in body
    assert body.index("山田 太郎") < body.index("Hanako Suzuki")


def test_researcher_detail_kaken_card(client):
    body = client.get("/researchers/A1").text
    assert "科研費（代表）" in body and "科研費（分担）" in body
    assert "7,500万円" in body
    body3 = client.get("/researchers/A3").text
    assert "科研費（代表）" in body3  # 0件でもカードは出る（金額は–）


def test_compare_page_basic(client):
    body = client.get("/compare?ids=A1,A2").text
    assert "山田 太郎" in body and "Hanako Suzuki" in body
    # 総被引用はA2(900)が最良
    assert '<td class="best">900</td>' in body
    # FWCI平均はA2がNone→数値1個のみ→ハイライトなし
    assert '<td class="best">3.50</td>' not in body
    # A2はtop_subfield None → 非NULLは1種類 → 注意なし
    assert "主分野が異なります" not in body
    assert "OpenAlex収録分に基づく" in body


def test_compare_order_and_subfield_warning(client):
    body = client.get("/compare?ids=A3,A1").text
    assert body.index("Ichiro Tanaka") < body.index("山田 太郎")  # 順序保持
    assert "主分野が異なります" in body  # ML vs Health Informatics


def test_compare_metricsless_and_dedupe(client):
    body = client.get("/compare?ids=A1,A4,A1").text
    assert "佐藤次郎" in body  # metrics無しでも列は出る
    assert body.count("山田 太郎") == 1  # 重複除去（列見出しに1回だけ）


def test_compare_insufficient_ids(client):
    for url in ("/compare", "/compare?ids=A1", "/compare?ids=,,bogus"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "2〜4人選んでください" in resp.text


def test_listing_pages_have_compare_controls(client):
    ranking = client.get("/?min_works=0").text
    assert 'class="cmp"' in ranking and 'data-id="A1"' in ranking
    assert 'id="compare-bar"' in ranking and "/static/compare.js" in ranking
    search = client.get("/search?q=yama").text
    assert 'class="cmp"' in search and 'data-id="A1"' in search


def test_compare_cap_applies_after_unknown_filter(client):
    body = client.get("/compare?ids=A1,BOGUS,A2,A3,A4").text
    # 不明ID除去後に4件へ切り詰めるので、A4（有効な4人目）は残る
    assert "佐藤次郎" in body
    assert "Ichiro Tanaka" in body


def test_compare_tie_highlights_both(client):
    # A2とA3のfirst_author_countは両者2で同値。同値タイはbestなしを検証する。
    # conftest でA2=2, A3=2 → 全員同値なのでbestなし
    import re
    body = client.get("/compare?ids=A2,A3").text
    # 筆頭著者数の行を抽出し、その行に best クラスがないことを確認:
    match = re.search(r'<th>筆頭著者数</th>.*?</tr>', body, re.DOTALL)
    assert match is not None, "筆頭著者数行が見つかりません"
    row = match.group(0)
    assert 'class="best"' not in row, f"筆頭著者数行に best クラスが含まれています: {row}"


def test_ranking_fwci_total_column_and_default(client):
    body = client.get("/").text
    assert "FWCI合計" in body
    assert "FWCI合計 ▼" in body  # 既定ソートのマーカー


def test_researcher_detail_ranks(client):
    body = client.get("/researchers/A1").text
    assert "学内1位" in body   # kaken_total_amount 75M は1位
    assert "学内2位" in body   # fwci_mean 3.5 は9.9に次ぐ2位
    body4 = client.get("/researchers/A4").text
    assert "学内" not in body4  # metrics無し→順位非表示


def test_compare_has_fwci_total_row(client):
    body = client.get("/compare?ids=A1,A3").text
    assert "FWCI合計" in body
    assert '<td class="best">35.00</td>' in body


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
    # A1(医学研究科)・A3(情報学研究科)ともに1人 < 最低人数しきい値のため「参考」表に回る
    assert "大学院医学研究科" in body and "大学院情報学研究科" in body
    assert body.index("大学院医学研究科") < body.index("大学院情報学研究科")
    assert "35.00" in body          # 人あたりFWCI合計
    assert "名寄せできた研究者のみ" in body
    assert "参考" in body
    assert "順位対象外" in body
    assert "/?department=" in body  # 部局リンク


def test_researcher_detail_shows_department(client):
    body = client.get("/researchers/A1").text
    assert "大学院医学研究科" in body and "教授" in body


def test_alias_redirects_to_canonical(client):
    resp = client.get("/researchers/A1b", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/researchers/A1"
