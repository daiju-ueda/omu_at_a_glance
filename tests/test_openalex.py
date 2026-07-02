import json

import httpx
import pytest

from collector.openalex import OpenAlexClient

PAGE1 = {"meta": {"count": 3, "next_cursor": "CUR2"},
         "results": [{"id": "https://openalex.org/A1"}, {"id": "https://openalex.org/A2"}]}
PAGE2 = {"meta": {"count": 3, "next_cursor": None},
         "results": [{"id": "https://openalex.org/A3"}]}


def make_client(handler):
    return OpenAlexClient(transport=httpx.MockTransport(handler), sleep_fn=lambda s: None)


def test_paginate_follows_cursor_and_sends_mailto():
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=PAGE2 if cursor == "CUR2" else PAGE1)

    client = make_client(handler)
    rows = list(client.paginate("authors", "last_known_institutions.id:I999"))
    assert [r["id"] for r in rows] == [
        "https://openalex.org/A1", "https://openalex.org/A2", "https://openalex.org/A3"]
    assert seen_params[0]["cursor"] == "*"
    assert seen_params[0]["mailto"] == "ai.labo.ocu@gmail.com"
    assert seen_params[1]["cursor"] == "CUR2"


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json=PAGE2)

    client = make_client(handler)
    rows = list(client.paginate("works", "f:x"))
    assert len(rows) == 1
    assert calls["n"] == 3


def test_gives_up_after_max_retries():
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = OpenAlexClient(transport=httpx.MockTransport(handler),
                            sleep_fn=sleeps.append)
    with pytest.raises(httpx.HTTPStatusError):
        list(client.paginate("works", "f:x"))
    assert sleeps == [1, 2, 4, 8, 16]


def test_retries_on_transport_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=PAGE2)

    client = make_client(handler)
    rows = list(client.paginate("works", "f:x"))
    assert len(rows) == 1
    assert calls["n"] == 3


def test_count():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["per-page"] == "1"
        return httpx.Response(200, json={"meta": {"count": 6222}, "results": []})

    client = make_client(handler)
    assert client.count("works", "f:x") == 6222
