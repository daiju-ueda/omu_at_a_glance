import logging
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://kaken.nii.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6


class KakenAuthError(Exception):
    pass


class KakenClient:
    def __init__(self, appid: str, transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._appid = appid
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport)

    def fetch(self, params: dict) -> str:
        params = {**params, "appid": self._appid}
        for attempt in range(MAX_TRIES):
            try:
                resp = self._http.get("/opensearch/", params=params)
            except httpx.TransportError:
                if attempt == MAX_TRIES - 1:
                    raise
                self._sleep(2 ** attempt)
                continue
            if resp.status_code == 403:
                raise KakenAuthError("KAKEN appid rejected (403)")
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_TRIES - 1:
                    break
                wait = 2 ** attempt
                logger.warning("KAKEN -> %s, retry in %ss",
                               resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            return resp.text
        resp.raise_for_status()
        raise RuntimeError("unreachable")
