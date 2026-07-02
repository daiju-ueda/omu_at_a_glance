import datetime
import logging
import re
import time
from collections import defaultdict

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import delete, select

from collector.nameutil import kana_part_variants, normalize_name
from db.models import GrantMember, Researcher, Roster, RosterAchievement, SyncState

logger = logging.getLogger(__name__)

BASE_URL = "https://kyoiku-kenkyudb.omu.ac.jp"
RETRY_STATUSES = {429, 500, 502, 503}
MAX_TRIES = 6
PAGE_SIZE = 10  # サイト固定のページサイズ（pp指定は無視される）
CRAWL_DELAY_SECONDS = 0.5  # 公式総覧への礼儀としてのアクセス間隔

DIVISION_HREF = re.compile(
    r"^/search\?m=affiliation&l=ja&a2=(\d+)&s=1&o=affiliation$")
TOTAL_RE = re.compile(r"([0-9,]+)\s*件中")
PROFILE_RE = re.compile(r"/html/(\d+)_ja\.html")
ACHIEVEMENT_SECTIONS = {
    "jusho": "award",
    "chosho": "book",
    "knkyu_prsn": "presentation",
    "gkkai_iinkai": "committee",
}
YEAR_RE = re.compile(r"(19|20)\d{2}")


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


def sync_roster(session, client, today: datetime.date) -> int:
    home = client.fetch("/")
    divisions = parse_divisions(home)
    if not divisions:
        logger.warning("roster: 部局リンクが見つからないためスキップ（既存データ保持）")
        return 0
    divisions.sort(key=lambda d: d[0])  # a2コード昇順で決定的に。重複所属者はコード最小の部局に帰属
    rows: dict[str, dict] = {}
    for code, label in divisions:
        page = 1
        while True:
            if page > 200:
                logger.warning("roster: ページ数上限(200)に達したため打ち切り (a2=%s)",
                               code)
                break
            html = client.fetch("/search", params={
                "m": "affiliation", "l": "ja", "a2": code,
                "s": 1, "p": page, "o": "affiliation"})
            members, total = parse_list_page(html)
            for m in members:
                if m["profile_id"] not in rows:
                    rows[m["profile_id"]] = {**m, "division": label}
            if members and total == 0:
                logger.warning("roster: 総件数が読めないため1ページで打ち切り (a2=%s)",
                               code)
                break
            if not members or page * PAGE_SIZE >= total:
                break
            page += 1
    if not rows:
        logger.warning("roster: 0件のため洗い替えをスキップ（既存データ保持）")
        return 0
    session.execute(delete(Roster))
    for row in rows.values():
        session.add(Roster(**row, updated_at=today.isoformat()))
    session.merge(SyncState(source="roster", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("roster sync done: %d人 / %d部局", len(rows), len(divisions))
    return len(rows)


def parse_profile_achievements(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    for section_id, category in ACHIEVEMENT_SECTIONS.items():
        container = soup.find("div", id=section_id)
        if container is None:
            continue
        for li in container.find_all("li"):
            title_el = li.find("p", class_="title")
            if title_el is None:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue
            detail = None
            for p in li.find_all("p", class_="contents"):
                if p.find_parent(class_="gaiyo-detail") is not None:
                    continue
                text = p.get_text(" ", strip=True)
                if text:
                    detail = text
                    break
            year_m = YEAR_RE.search(detail or "")
            entries.append({
                "category": category,
                "title": title,
                "year": int(year_m.group(0)) if year_m else None,
                "detail": detail,
            })
    return entries


def sync_profiles(session, client, today: datetime.date) -> int:
    profile_ids = list(session.scalars(select(Roster.profile_id)))
    if not profile_ids:
        logger.warning("profiles: rosterが空のためスキップ")
        return 0
    rows: list[dict] = []
    ok = 0
    for pid in profile_ids:
        try:
            html = client.fetch(f"/html/{pid}_ja.html")
        except Exception as e:
            logger.warning("profileページ取得失敗 %s: %s", pid, e)
            continue
        ok += 1
        for entry in parse_profile_achievements(html):
            rows.append({**entry, "profile_id": pid})
    if ok < len(profile_ids) * 0.5:
        logger.warning(
            "profiles: 取得成功が5割未満（%d/%d）のため洗い替えをスキップ",
            ok, len(profile_ids))
        return 0
    session.execute(delete(RosterAchievement))
    for row in rows:
        session.add(RosterAchievement(**row, updated_at=today.isoformat()))
    session.merge(SyncState(source="profiles", cursor=None,
                            last_synced_at=today.isoformat()))
    session.commit()
    logger.info("profiles: %d人分 %d件の実績", ok, len(rows))
    return len(rows)


def match_roster(session) -> int:
    # 退職者などが残した古いname_jaを信用して誤って復活マッチしないよう、
    # 「現在も公式総覧に載っている」または「KAKENで現に一致している」名前のみ
    # kanji_indexに採用する
    kaken_named = set(session.scalars(
        select(GrantMember.matched_researcher_id)
        .where(GrantMember.matched_researcher_id.is_not(None))
        .distinct()))

    name_index: dict[str, set[str]] = defaultdict(set)
    kanji_index: dict[str, set[str]] = defaultdict(set)
    for rid, display_name, name_ja, is_official in session.execute(
            select(Researcher.openalex_id, Researcher.display_name,
                   Researcher.name_ja, Researcher.is_official_roster)
            .where(Researcher.canonical_id.is_(None))):
        name_index[normalize_name(display_name)].add(rid)
        if name_ja and (rid in kaken_named or is_official):
            kanji_index[name_ja].add(rid)

    entries = list(session.scalars(select(Roster)))
    proposals: dict[str, list] = defaultdict(list)  # rid -> [Roster, ...]
    for entry in entries:
        entry.matched_researcher_id = None
        candidates: set[str] = set(kanji_index.get(entry.name_kanji, set()))
        if entry.name_kana:
            parts = entry.name_kana.replace("　", " ").split()
            if len(parts) == 2:
                for fam in kana_part_variants(parts[0]):
                    for giv in kana_part_variants(parts[1]):
                        candidates |= name_index.get(
                            normalize_name(f"{giv} {fam}"), set())
                        candidates |= name_index.get(
                            normalize_name(f"{fam} {giv}"), set())
        if len(candidates) == 1:
            proposals[candidates.pop()].append(entry)

    matched = 0
    matched_rids: set[str] = set()
    for rid, claimants in proposals.items():
        if len(claimants) != 1:
            continue  # 学内同姓同名の衝突 → 全て未マッチのまま
        entry = claimants[0]
        researcher = session.get(Researcher, rid)
        if researcher is None:
            continue
        entry.matched_researcher_id = rid
        researcher.name_ja = entry.name_kanji
        researcher.department = entry.division
        researcher.position = entry.position
        researcher.is_official_roster = True
        matched_rids.add(rid)
        matched += 1

    # 以前rosterマッチだったが今回外れた研究者のクリア（name_jaは残す）
    for researcher in session.scalars(
            select(Researcher).where(
                Researcher.is_official_roster.is_(True))):
        if researcher.openalex_id not in matched_rids:
            researcher.department = None
            researcher.position = None
            researcher.is_official_roster = False
    session.commit()
    logger.info("roster名寄せ: %d人を確定", matched)
    return matched
