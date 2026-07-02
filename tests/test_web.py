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
    assert "Taro Yamada" in body
    assert "Ichiro Tanaka" not in body  # works<5は既定で除外
    assert body.index("Taro Yamada") < body.index("Hanako Suzuki")  # NULL FWCI末尾
    assert "最終同期: 2026-07-02" in body
    assert "OpenAlex収録分に基づく" in body


def test_ranking_sort_and_min_works(client):
    body = client.get("/?sort=total_citations&min_works=0").text
    assert body.index("Hanako Suzuki") < body.index("Taro Yamada")
    assert "Ichiro Tanaka" in body


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
