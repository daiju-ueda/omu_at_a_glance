import datetime
import json
import logging

from sqlalchemy import delete, select

from collector.parse import omu_raw_author_ids, parse_author
from collector.sync import AUTHOR_SELECT, _record_state, _upsert
from db.models import Researcher, Work

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # ids.openalexフィルタの1リクエストあたり著者数


def qualifying_author_ids(session) -> set[str]:
    """raw所属文字列がOMU系のauthorshipを1件以上持つ著者ID

    OpenAlexの解決済み機関IDは誤解決が多く（阪大の論文が旧市大IDに付く等）、
    IDベースの判定は学外者を取り込むため、rawの所属表記で判定する。
    """
    qualifying: set[str] = set()
    for (raw,) in session.execute(select(Work.raw_json)):
        qualifying |= omu_raw_author_ids(json.loads(raw))
    return qualifying


def backfill_authors(session, client, today: datetime.date) -> int:
    qualifying = qualifying_author_ids(session)
    last_known = set(session.scalars(
        select(Researcher.openalex_id)
        .where(Researcher.source == "last_known")))
    to_fetch = sorted(qualifying - last_known)
    n = 0
    for i in range(0, len(to_fetch), BATCH_SIZE):
        batch = to_fetch[i:i + BATCH_SIZE]
        filter_str = "ids.openalex:" + "|".join(batch)
        got: set[str] = set()
        for rec in client.paginate("authors", filter_str,
                                   select=AUTHOR_SELECT, per_page=BATCH_SIZE):
            kw = parse_author(rec)
            got.add(kw["openalex_id"])
            _upsert(session, Researcher, {**kw, "source": "works"})
            n += 1
        skipped = set(batch) - got
        if skipped:
            logger.warning(
                "backfill: %d件の著者が取得できずスキップ（マージ消滅等）: %s",
                len(skipped), sorted(skipped)[:5])
        session.commit()

    works_sourced = set(session.scalars(
        select(Researcher.openalex_id).where(Researcher.source == "works")))
    stale = works_sourced - qualifying
    if stale:
        session.execute(
            delete(Researcher).where(Researcher.openalex_id.in_(stale)))
        logger.info("backfill: %d件のworks由来研究者を削除（対象authorship消滅）",
                    len(stale))
    _record_state(session, "backfill", today)
    logger.info("backfill done: %d人補完/更新（対象著者%d人）", n, len(qualifying))
    return n
