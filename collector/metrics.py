import collections
import datetime
import statistics

from sqlalchemy import delete, select

from collector.sync import window_start
from db.models import Authorship, Researcher, ResearcherMetrics, Work


def _rate(count, total):
    return round(count / total, 4) if total else None


def compute_metrics(session, today: datetime.date) -> int:
    start = window_start(today)
    session.execute(delete(ResearcherMetrics))

    rows = session.execute(
        select(Authorship.author_id, Authorship.author_position,
               Authorship.is_corresponding, Work.openalex_id,
               Work.cited_by_count, Work.fwci, Work.is_top10pct,
               Work.is_top1pct, Work.n_authors, Work.is_intl_collab,
               Work.is_corp_collab, Work.is_oa, Work.type, Work.subfield,
               Work.is_authors_truncated)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .join(Researcher, Researcher.openalex_id == Authorship.author_id)
        .where(Work.publication_date >= start)
    ).all()

    # ウィンドウ内の各workの全著者（外部共著者含む）
    by_work: dict[str, set[str]] = {}
    for work_id, author_id in session.execute(
        select(Authorship.work_id, Authorship.author_id)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .where(Work.publication_date >= start)
    ):
        by_work.setdefault(work_id, set()).add(author_id)

    by_author: dict[str, list] = {}
    for row in rows:
        by_author.setdefault(row.author_id, []).append(row)

    n = 0
    for rid in session.scalars(select(Researcher.openalex_id)):
        items = by_author.get(rid, [])
        n_works = len(items)
        fwcis = [r.fwci for r in items if r.fwci is not None]
        countable = [r for r in items if not r.is_authors_truncated]
        divisors = [max(r.n_authors, 1) for r in countable]
        partners: set[str] = set()
        for r in items:
            partners |= by_work.get(r.openalex_id, set())
        partners.discard(rid)
        top_subfield = None
        subfields = [r.subfield for r in items if r.subfield]
        if subfields:
            counts = collections.Counter(subfields)
            best = max(counts.values())
            top_subfield = min(k for k, v in counts.items() if v == best)
        session.add(ResearcherMetrics(
            researcher_id=rid,
            works_count_3y=n_works,
            total_citations=sum(r.cited_by_count for r in items),
            fwci_mean=round(statistics.mean(fwcis), 4) if fwcis else None,
            fwci_median=round(statistics.median(fwcis), 4) if fwcis else None,
            top10pct_count=sum(1 for r in items if r.is_top10pct),
            top1pct_count=sum(1 for r in items if r.is_top1pct),
            first_author_count=sum(
                1 for r in items if r.author_position == "first"),
            corresponding_count=sum(1 for r in items if r.is_corresponding),
            fractional_works=round(sum(1 / d for d in divisors), 4),
            fractional_citations=round(
                sum(r.cited_by_count / d for r, d in zip(countable, divisors)), 4),
            avg_authors=round(statistics.mean(divisors), 4) if countable else None,
            intl_collab_rate=_rate(
                sum(1 for r in items if r.is_intl_collab), n_works),
            corp_collab_rate=_rate(
                sum(1 for r in items if r.is_corp_collab), n_works),
            oa_rate=_rate(sum(1 for r in items if r.is_oa), n_works),
            preprint_count=sum(1 for r in items if r.type == "preprint"),
            dataset_software_count=sum(
                1 for r in items if r.type in ("dataset", "software")),
            unique_coauthors=len(partners),
            top_subfield=top_subfield,
            computed_at=today.isoformat(),
        ))
        n += 1
    session.commit()
    return n
