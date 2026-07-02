import logging
import time
from collections.abc import Iterator

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6


class OpenAlexClient:
    def __init__(self, mailto: str = "ai.labo.ocu@gmail.com",
                 transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._mailto = mailto
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport)

    def _get(self, endpoint: str, params: dict) -> dict:
        params = {**params, "mailto": self._mailto}
        for attempt in range(MAX_TRIES):
            try:
                resp = self._http.get(f"/{endpoint}", params=params)
            except httpx.TransportError as exc:
                if attempt == MAX_TRIES - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("OpenAlex %s -> %s, retry in %ss",
                               endpoint, exc, wait)
                self._sleep(wait)
                continue
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_TRIES - 1:
                    break
                wait = 2 ** attempt
                logger.warning("OpenAlex %s -> %s, retry in %ss",
                               endpoint, resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()  # 最終試行のエラーを送出
        raise RuntimeError("unreachable")

    def paginate(self, endpoint: str, filter_str: str,
                 select: str | None = None, per_page: int = 200) -> Iterator[dict]:
        cursor = "*"
        while cursor:
            params = {"filter": filter_str, "per-page": per_page, "cursor": cursor}
            if select:
                params["select"] = select
            data = self._get(endpoint, params)
            yield from data["results"]
            cursor = data["meta"].get("next_cursor")

    def count(self, endpoint: str, filter_str: str) -> int:
        data = self._get(endpoint, {"filter": filter_str, "per-page": 1})
        return data["meta"]["count"]
