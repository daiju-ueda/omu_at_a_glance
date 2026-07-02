import datetime

from sqlalchemy import func, or_, select

from collector.sync import window_start
from db.models import (Authorship, Researcher, ResearcherMetrics, SyncState,
                       Work)

PAGE_SIZE = 100

SORT_COLUMNS = {
    "fwci_mean": ResearcherMetrics.fwci_mean,
    "total_citations": ResearcherMetrics.total_citations,
    "top10pct_count": ResearcherMetrics.top10pct_count,
    "works_count_3y": ResearcherMetrics.works_count_3y,
    "fractional_citations": ResearcherMetrics.fractional_citations,
}


def ranking(session, sort="fwci_mean", min_works=1, page=1):
    col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_mean)
    cond = ResearcherMetrics.works_count_3y >= min_works
    total_all = session.scalar(
        select(func.count()).select_from(ResearcherMetrics))
    total = session.scalar(
        select(func.count()).select_from(ResearcherMetrics).where(cond))
    rows = session.execute(
        select(Researcher, ResearcherMetrics)
        .join(ResearcherMetrics,
              ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(cond)
        # SQLiteはNULLを最小値として扱うため、DESCでNULLは自然に末尾になる
        .order_by(col.desc(), Researcher.openalex_id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return rows, total, total_all


def researcher_detail(session, openalex_id, today=None):
    researcher = session.get(Researcher, openalex_id)
    if researcher is None:
        return None
    metrics = session.get(ResearcherMetrics, openalex_id)
    start = window_start(today or datetime.date.today())
    works = session.execute(
        select(Work, Authorship)
        .join(Authorship, Authorship.work_id == Work.openalex_id)
        .where(Authorship.author_id == openalex_id,
               Work.publication_date >= start)
        .order_by(Work.cited_by_count.desc(), Work.openalex_id)
    ).all()
    return researcher, metrics, works


def search(session, q, limit=200):
    pattern = f"%{q}%"
    return session.execute(
        select(Researcher, ResearcherMetrics)
        .outerjoin(ResearcherMetrics,
                   ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(or_(Researcher.display_name.ilike(pattern),
                   Researcher.name_ja.ilike(pattern)))
        .order_by(ResearcherMetrics.fwci_mean.desc(), Researcher.openalex_id)
        .limit(limit)
    ).all()


def last_synced(session):
    state = session.get(SyncState, "works")
    return state.last_synced_at if state else None
