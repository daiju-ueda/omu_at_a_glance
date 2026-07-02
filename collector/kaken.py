import json
import logging
import re as _re
import time
import xml.etree.ElementTree as ET

import httpx

# XXE/billion-laughs対策: パースは必ずdefusedxml経由で行う
# （ET.tostringは既にパース済みのtreeの直列化なのでstdlibで安全）
from defusedxml.ElementTree import fromstring as _safe_fromstring

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


def _strip_namespaces(xml_text: str) -> str:
    return _re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", xml_text)


def _int_or_none(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_grants(xml_text: str) -> tuple[list[tuple[dict, list[dict]]], int]:
    root = _safe_fromstring(_strip_namespaces(xml_text))
    total = _int_or_none(root.get("total")) or 0
    entries = []
    for ga in root.iter("grantAward"):
        try:
            award_id = ga.get("awardNumber") or ""
            if not award_id:
                raise ValueError("awardNumber missing")
            summary = ga.find("summary")
            if summary is None:
                raise ValueError("summary missing")
            period = summary.find("periodOfAward")
            grant = {
                "award_id": award_id,
                "title": (summary.findtext("title") or "").strip(),
                "category": summary.findtext("category"),
                "institution": (summary.findtext("institution") or "").strip(),
                "start_year": _int_or_none(
                    period.findtext("startFiscalYear")) if period is not None else None,
                "end_year": _int_or_none(
                    period.findtext("endFiscalYear")) if period is not None else None,
                "total_amount": _int_or_none(
                    summary.findtext("overallAwardAmount/totalCost")) or 0,
                "raw_json": json.dumps(
                    ET.tostring(ga, encoding="unicode"), ensure_ascii=False),
            }
            members = []
            for m in summary.iter("member"):
                name_kanji = (m.findtext("personalName/fullName") or "").strip()
                if not name_kanji:
                    continue
                erad = (m.get("eradCode") or "").strip()
                kana = m.findtext("personalName/nameKana")
                role = m.get("role") or ""
                members.append({
                    "award_id": award_id,
                    "erad_id": erad or f"name:{name_kanji}",
                    "name_kanji": name_kanji,
                    "name_kana": kana.strip() if kana else None,
                    "role": ("principal" if "principal_investigator" in role
                             else "co_investigator"),
                })
            entries.append((grant, members))
        except (ValueError, AttributeError) as e:
            logger.warning("grantAwardのパースをスキップ: %s", e)
    return entries, total
