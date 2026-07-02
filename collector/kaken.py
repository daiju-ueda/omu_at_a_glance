import datetime
import json
import logging
import re as _re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import httpx

# XXE/billion-laughs対策: パースは必ずdefusedxml経由で行う
# （ET.tostringは既にパース済みのtreeの直列化なのでstdlibで安全）
from defusedxml.ElementTree import fromstring as _safe_fromstring
from sqlalchemy import delete, select

from collector.nameutil import kana_part_variants, normalize_name
from collector.sync import window_start
from db.models import Grant, GrantMember, Researcher, SyncState

logger = logging.getLogger(__name__)

BASE_URL = "https://kaken.nii.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6
PAGE_SIZE_KAKEN = 500
# KAKEN APIのrwは{20,50,100,200,500}のみ受理。それ以外は「エラーではなく0件」を
# 静かに返すため、この値を変えるときは必ずenum内の値にすること
# 実XMLの<summary>はja/en両方を含むため、xml:lang="ja"のsummaryを明示的に選ぶ
_JA_SUMMARY_XPATH = "summary[@{http://www.w3.org/XML/1998/namespace}lang='ja']"


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
        # format=xmlを付けない場合、KAKENはHTML検索画面を返す（実API照合で確認済み）
        params = {"format": "xml", **params, "appid": self._appid}
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
    # 総件数は<grantAwards total="...">属性ではなく<totalResults>子要素（実API照合で確認済み）
    total = _int_or_none(root.findtext("totalResults")) or 0
    entries = []
    for ga in root.iter("grantAward"):
        try:
            award_id = ga.get("awardNumber") or ""
            if not award_id:
                raise ValueError("awardNumber missing")
            # <summary>はja/en両方が入るため、xml:lang="ja"を明示的に選ぶ
            summary = ga.find(_JA_SUMMARY_XPATH)
            if summary is None:
                raise ValueError("summary missing")
            period = summary.find("periodOfAward")
            grant = {
                "award_id": award_id,
                "title": (summary.findtext("title") or "").strip(),
                "category": summary.findtext("category"),
                "institution": (summary.findtext("institution") or "").strip(),
                # 実APIではperiodOfAwardの子要素ではなく属性
                "start_year": _int_or_none(
                    period.get("searchStartFiscalYear")) if period is not None else None,
                "end_year": _int_or_none(
                    period.get("searchEndFiscalYear")) if period is not None else None,
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
                # 実APIには<nameKana>は無く、familyName/givenNameのyomi属性がカナ
                family_el = m.find("personalName/familyName")
                given_el = m.find("personalName/givenName")
                family_kana = family_el.get("yomi") if family_el is not None else None
                given_kana = given_el.get("yomi") if given_el is not None else None
                kana = (f"{family_kana} {given_kana}"
                        if family_kana and given_kana else None)
                role = m.get("role") or ""
                # memberの直接の子<institution>が所属機関（affiliation配下のものと区別するため
                # 直下のみを見る）
                institution = m.findtext("institution")
                members.append({
                    "award_id": award_id,
                    "erad_id": erad or f"name:{name_kanji}",
                    "name_kanji": name_kanji,
                    "name_kana": kana,
                    "institution": institution.strip() if institution else None,
                    "role": ("principal" if "principal_investigator" in role
                             else "co_investigator"),
                })
            entries.append((grant, members))
        except (ValueError, AttributeError) as e:
            logger.warning("grantAwardのパースをスキップ: %s", e)
    return entries, total


def sync_kaken(session, client, today: datetime.date,
               institution: str = "大阪公立大学") -> int:
    window_year = int(window_start(today)[:4])
    current_year = today.year
    entries: list[tuple[dict, list[dict]]] = []
    st = 1
    while True:
        # kwは全文検索でノイズが多い（他機関の課題も拾う）。qe（研究機関）が正確
        xml_text = client.fetch({"qe": institution,
                                 "rw": PAGE_SIZE_KAKEN, "st": st})
        page_entries, total = parse_grants(xml_text)
        entries.extend(page_entries)
        st += PAGE_SIZE_KAKEN
        if st > total:
            break

    kept = []
    for grant, members in entries:
        if grant.pop("institution", "") != institution:
            continue
        end = grant["end_year"]
        start = grant["start_year"]
        if end is not None and end < window_year:
            continue
        if start is not None and start > current_year:
            continue
        kept.append((grant, members))

    if not kept:
        logger.warning("KAKEN: 0件のため洗い替えをスキップ（既存データ保持）")
        return 0

    session.execute(delete(GrantMember))
    session.execute(delete(Grant))
    for grant, members in kept:
        session.add(Grant(**grant, updated_at=today.isoformat()))
        seen_member_keys = set()
        for m in members:
            key = (m["award_id"], m["erad_id"])
            if key in seen_member_keys:
                continue
            seen_member_keys.add(key)
            session.add(GrantMember(**m))
    session.merge(SyncState(source="kaken", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("KAKEN sync done: %d grants", len(kept))
    return len(kept)


OMU_INSTITUTION = "大阪公立大学"


def match_members(session) -> int:
    index: dict[str, set[str]] = defaultdict(set)
    for rid, name in session.execute(
            select(Researcher.openalex_id, Researcher.display_name)):
        index[normalize_name(name)].add(rid)

    # rid -> このrunで一意マッチした GrantMember のリスト（後で衝突検知に使う）
    rid_members: dict[str, list] = defaultdict(list)
    rid_kanji: dict[str, set[str]] = defaultdict(set)

    for member in session.scalars(select(GrantMember)):
        member.matched_researcher_id = None
        # 実XMLのmemberには所属機関が明示される。他機関の同姓同名による誤爆を防ぐため、
        # 本学所属のメンバーのみをマッチ対象にする
        if member.institution != OMU_INSTITUTION:
            continue
        if not member.name_kana:
            continue
        parts = member.name_kana.replace("　", " ").split()
        if len(parts) != 2:
            continue
        family_variants = kana_part_variants(parts[0])
        given_variants = kana_part_variants(parts[1])
        candidates: set[str] = set()
        for fam in family_variants:
            for giv in given_variants:
                candidates |= index.get(normalize_name(f"{giv} {fam}"), set())
                candidates |= index.get(normalize_name(f"{fam} {giv}"), set())
        if len(candidates) == 1:
            rid = candidates.pop()
            rid_members[rid].append(member)
            rid_kanji[rid].add(member.name_kanji.replace("　", " "))

    matched = 0
    name_ja_by_rid: dict[str, str] = {}
    for rid, members in rid_members.items():
        kanji_variants = rid_kanji[rid]
        if len(kanji_variants) > 1:
            # 同一研究者に異なる漢字表記で claim されている＝別人の可能性が高く、
            # 誰の名前かも確定できないため全員unmatchのままにする（識別子不確実）
            logger.warning(
                "KAKEN名寄せ: %sに漢字表記が衝突するclaimあり（%s）→unmatch",
                rid, sorted(kanji_variants))
            continue
        for member in members:
            # matched_researcher_id（grant紐付け）はガードの前に設定する:
            # 公式総覧優先の対象はname_jaの上書きのみで、grant⇔researcherの
            # マッチ自体は公式総覧の有無に関わらず有効にしたい
            member.matched_researcher_id = rid
            matched += 1
        name_ja_by_rid[rid] = next(iter(kanji_variants))

    # name_ja の全件再計算: フェーズ2の公式名簿が無い現状ではKAKENが唯一のソースなので、
    # 今回マッチしなかった研究者のname_jaは（過去に付いていても）クリアするのが正しい。
    # ただし公式総覧（is_official_roster=True）で確定した研究者のname_jaはKAKENより
    # 信頼できるため、KAKEN側では一切触らない
    for researcher in session.scalars(
            select(Researcher).where(
                Researcher.name_ja.is_not(None),
                Researcher.is_official_roster.is_(False))):
        if researcher.openalex_id not in name_ja_by_rid:
            researcher.name_ja = None

    for rid, name_ja in name_ja_by_rid.items():
        researcher = session.get(Researcher, rid)
        if researcher is None or researcher.is_official_roster:
            continue
        researcher.name_ja = name_ja
    session.commit()
    logger.info("KAKEN名寄せ: %d人を一意マッチ", matched)
    return matched
