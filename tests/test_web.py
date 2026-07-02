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
    assert body.index("Ichiro Tanaka") < body.index("Taro Yamada")  # fwci 9.9先頭
    assert body.index("Taro Yamada") < body.index("Hanako Suzuki")  # NULL末尾
    assert "全3人中 3人を表示" in body
    assert "最終同期: 2026-07-02" in body
    assert "OpenAlex収録分に基づく" in body
    assert "重複計上される場合がある" in body


def test_ranking_sort_and_min_works(client):
    body = client.get("/?sort=total_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")
    body5 = client.get("/?min_works=5").text
    assert "全3人中 2人を表示" in body5
    assert "Ichiro Tanaka" not in body5


def test_ranking_fractional_sort_and_top1_column(client):
    body = client.get("/?sort=fractional_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")  # 300>120.5
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
    assert "Taro Yamada" in resp.text

    resp = client.get("/?page=99999999999999999999&min_works=99999999999999999999")
    assert resp.status_code == 200
    assert "Taro Yamada" in resp.text


def test_researcher_detail(client):
    body = client.get("/researchers/A1").text
    assert "Taro Yamada" in body
    assert "Deep Learning in Radiology" in body
    assert "Old Paper Outside Window" not in body  # ウィンドウ外
    assert "orcid.org/0000-0001-1111-1111" in body
    assert "top1%" in body
    assert "–" in body  # W3のFWCI欠損表示


def test_researcher_404(client):
    resp = client.get("/researchers/NOPE")
    assert resp.status_code == 404


def test_search(client):
    assert "Taro Yamada" in client.get("/search?q=yama").text
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
