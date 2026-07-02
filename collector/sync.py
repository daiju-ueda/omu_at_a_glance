import datetime
import logging

from sqlalchemy.dialects.sqlite import insert

from collector.parse import parse_author, parse_work
from db.models import Authorship, Researcher, SyncState, Work

logger = logging.getLogger(__name__)

INSTITUTION_ID = "I4387152983"  # 大阪公立大学
AUTHOR_SELECT = "id,display_name,orcid,works_count,summary_stats,updated_date"
WORK_SELECT = (
    "id,doi,title,publication_date,type,cited_by_count,fwci,"
    "citation_normalized_percentile,primary_topic,primary_location,"
    "open_access,authorships,updated_date"
)
COMMIT_EVERY = 1000


def window_start(today: datetime.date) -> str:
    try:
        return today.replace(year=today.year - 3).isoformat()
    except ValueError:  # 2/29 で3年前が平年の場合
        return today.replace(year=today.year - 3, day=28).isoformat()


def _upsert(session, model, kwargs: dict):
    stmt = insert(model).values(**kwargs)
    pk_cols = [c.name for c in model.__table__.primary_key]
    update_cols = {k: v for k, v in kwargs.items() if k not in pk_cols}
    session.execute(stmt.on_conflict_do_update(
        index_elements=pk_cols, set_=update_cols))


def _record_state(session, source: str, today: datetime.date):
    _upsert(session, SyncState,
            {"source": source, "cursor": None, "last_synced_at": today.isoformat()})
    session.commit()


def sync_authors(session, client, today: datetime.date,
                 institution_id: str = INSTITUTION_ID,
                 since: str | None = None) -> int:
    filter_str = f"last_known_institutions.id:{institution_id}"
    if since:
        filter_str += f",from_updated_date:{since}"
    n = 0
    for rec in client.paginate("authors", filter_str, select=AUTHOR_SELECT):
        _upsert(session, Researcher, parse_author(rec))
        n += 1
        if n % COMMIT_EVERY == 0:
            session.commit()
            logger.info("authors: %d upserted", n)
    _record_state(session, "authors", today)
    logger.info("authors sync done: %d", n)
    return n


def sync_works(session, client, today: datetime.date,
               institution_id: str = INSTITUTION_ID,
               since: str | None = None) -> int:
    filter_str = (f"institutions.id:{institution_id},"
                  f"from_publication_date:{window_start(today)}")
    if since:
        filter_str += f",from_updated_date:{since}"
    n = 0
    for rec in client.paginate("works", filter_str, select=WORK_SELECT):
        work_kw, authorships = parse_work(rec)
        _upsert(session, Work, work_kw)
        for a in authorships:
            _upsert(session, Authorship, a)
        n += 1
        if n % COMMIT_EVERY == 0:
            session.commit()
            logger.info("works: %d upserted", n)
    _record_state(session, "works", today)
    if since is None:  # full同期時のみ全件数を突合
        expected = client.count("works", filter_str)
        if expected != n:
            logger.warning("works count mismatch: api=%d local=%d", expected, n)
    logger.info("works sync done: %d", n)
    return n
