import httpx
import pytest

from collector.kaken import KakenAuthError, KakenClient

XML_OK = '<?xml version="1.0"?><grantAwards><totalResults>0</totalResults></grantAwards>'


def make_client(handler):
    return KakenClient("APPID_X", transport=httpx.MockTransport(handler),
                       sleep_fn=lambda s: None)


def test_fetch_sends_appid_and_returns_xml():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, text=XML_OK)

    client = make_client(handler)
    body = client.fetch({"qe": "大阪公立大学", "rw": 500, "st": 1})
    assert body == XML_OK
    assert seen["appid"] == "APPID_X"
    assert seen["qe"] == "大阪公立大学"
    # format=xmlを付けない場合、KAKENはHTML検索画面を返す（実API照合で確認済み）
    assert seen["format"] == "xml"


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
