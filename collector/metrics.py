import datetime
import statistics

from sqlalchemy import delete, select

from collector.sync import window_start
from db.models import Authorship, Researcher, ResearcherMetrics, Work


def compute_metrics(session, today: datetime.date) -> int:
    start = window_start(today)
    session.execute(delete(ResearcherMetrics))

    rows = session.execute(
        select(Authorship.author_id, Authorship.author_position,
               Authorship.is_corresponding, Work.cited_by_count,
               Work.fwci, Work.is_top10pct)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .join(Researcher, Researcher.openalex_id == Authorship.author_id)
        .where(Work.publication_date >= start)
    ).all()

    by_author: dict[str, list] = {}
    for row in rows:
        by_author.setdefault(row.author_id, []).append(row)

    n = 0
    for rid in session.scalars(select(Researcher.openalex_id)):
        items = by_author.get(rid, [])
        fwcis = [r.fwci for r in items if r.fwci is not None]
        session.add(ResearcherMetrics(
            researcher_id=rid,
            works_count_3y=len(items),
            total_citations=sum(r.cited_by_count for r in items),
            fwci_mean=round(statistics.mean(fwcis), 4) if fwcis else None,
            fwci_median=round(statistics.median(fwcis), 4) if fwcis else None,
            top10pct_count=sum(1 for r in items if r.is_top10pct),
            first_author_count=sum(1 for r in items if r.author_position == "first"),
            corresponding_count=sum(1 for r in items if r.is_corresponding),
            computed_at=today.isoformat(),
        ))
        n += 1
    session.commit()
    return n
