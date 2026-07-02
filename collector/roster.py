import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://kyoiku-kenkyudb.omu.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6
PAGE_SIZE = 100
CRAWL_DELAY_SECONDS = 0.5  # 公式総覧への礼儀としてのアクセス間隔

DIVISION_HREF = re.compile(
    r"^/search\?m=affiliation&l=ja&a2=(\d+)&s=1&o=affiliation$")
TOTAL_RE = re.compile(r"([0-9,]+)\s*件中")
PROFILE_RE = re.compile(r"/html/(\d+)_ja\.html")


class RosterClient:
    def __init__(self, transport: httpx.BaseTransport | None = None,
                 sleep_fn=time.sleep):
        self._sleep = sleep_fn
        self._http = httpx.Client(base_url=BASE_URL, timeout=60,
                                  transport=transport, follow_redirects=True)

    def fetch(self, path: str, params: dict | None = None) -> str:
        for attempt in range(MAX_TRIES):
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt == MAX_TRIES - 1:
                    raise
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_TRIES - 1:
                    break
                wait = 2 ** attempt
                logger.warning("roster -> %s, retry in %ss",
                               resp.status_code, wait)
                self._sleep(wait)
                continue
            resp.raise_for_status()
            self._sleep(CRAWL_DELAY_SECONDS)
            return resp.text
        resp.raise_for_status()
        raise RuntimeError("unreachable")


def parse_divisions(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    divisions: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = DIVISION_HREF.match(a["href"])
        if not m:
            continue
        code = m.group(1)
        label = a.get_text(strip=True)
        if code in seen or not label:
            continue
        seen.add(code)
        divisions.append((code, label))
    return divisions


def parse_list_page(html: str) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    total_m = TOTAL_RE.search(soup.get_text())
    total = int(total_m.group(1).replace(",", "")) if total_m else 0
    members: list[dict] = []
    for card in soup.select("div.card-body.result"):
        link = card.select_one(".name-gng a[href]")
        if link is None:
            logger.warning("rosterカードをスキップ: 氏名リンクなし")
            continue
        pm = PROFILE_RE.search(link["href"])
        if pm is None:
            logger.warning("rosterカードをスキップ: profile ID不明 (%s)",
                           link["href"])
            continue
        kana_el = card.select_one(".name-kna")
        title_el = card.select_one(".name-title")
        org_el = card.select_one(".org-cd")
        kw_el = card.select_one(".kaknh-bnrui")
        kana = kana_el.get_text(strip=True) if kana_el else None
        position = title_el.get_text(strip=True) if title_el else None
        members.append({
            "profile_id": pm.group(1),
            "name_kanji": link.get_text(strip=True).replace("　", " "),
            "name_kana": kana or None,
            "position": position or None,
            "org_text": org_el.get_text(" ", strip=True) if org_el else None,
            "keywords": kw_el.get_text(strip=True) if kw_el else None,
        })
    return members, total
