import collections
import datetime
import statistics

from sqlalchemy import delete, select

from collector.sync import window_start
from db.models import Authorship, Grant, GrantMember, Researcher, ResearcherMetrics, Roster, RosterAchievement, Work


def _rate(count, total):
    return round(count / total, 4) if total else None


def compute_metrics(session, today: datetime.date) -> int:
    start = window_start(today)
    session.execute(delete(ResearcherMetrics))

    alias_map: dict[str, str] = {
        row.openalex_id: row.canonical_id
        for row in session.execute(
            select(Researcher.openalex_id, Researcher.canonical_id)
            .where(Researcher.canonical_id.is_not(None)))
    }

    def canon(author_id: str) -> str:
        return alias_map.get(author_id, author_id)

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

    # ウィンドウ内の各workの全著者（外部共著者含む、正準化済み）
    by_work: dict[str, set[str]] = {}
    for work_id, author_id in session.execute(
        select(Authorship.work_id, Authorship.author_id)
        .join(Work, Work.openalex_id == Authorship.work_id)
        .where(Work.publication_date >= start)
    ):
        by_work.setdefault(work_id, set()).add(canon(author_id))

    # (正準著者, work) 単位に集約し、first/correspondingはOR
    per_author_work: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (canon(row.author_id), row.openalex_id)
        agg = per_author_work.get(key)
        if agg is None:
            per_author_work[key] = {
                "row": row,
                "first": row.author_position == "first",
                "corresponding": bool(row.is_corresponding),
            }
        else:
            agg["first"] = agg["first"] or row.author_position == "first"
            agg["corresponding"] = (agg["corresponding"]
                                    or bool(row.is_corresponding))

    by_author: dict[str, list] = {}
    for (author_id, _work_id), agg in per_author_work.items():
        by_author.setdefault(author_id, []).append(agg)

    kaken_by_author: dict[str, list] = {}
    for row in session.execute(
        select(GrantMember.matched_researcher_id, GrantMember.role,
               Grant.total_amount)
        .join(Grant, Grant.award_id == GrantMember.award_id)
        .where(GrantMember.matched_researcher_id.is_not(None))
    ):
        kaken_by_author.setdefault(canon(row.matched_researcher_id), []).append(row)

    ach_by_author: dict[str, dict] = {}
    for rid, category in session.execute(
        select(Roster.matched_researcher_id, RosterAchievement.category)
        .join(RosterAchievement,
              RosterAchievement.profile_id == Roster.profile_id)
        .where(Roster.matched_researcher_id.is_not(None))
    ):
        counts = ach_by_author.setdefault(canon(rid), {})
        counts[category] = counts.get(category, 0) + 1

    n = 0
    for rid in session.scalars(select(Researcher.openalex_id)
                               .where(Researcher.canonical_id.is_(None))):
        items = by_author.get(rid, [])
        n_works = len(items)
        rows_only = [it["row"] for it in items]
        fwcis = [r.fwci for r in rows_only if r.fwci is not None]
        countable = [r for r in rows_only if not r.is_authors_truncated]
        divisors = [max(r.n_authors, 1) for r in countable]
        partners: set[str] = set()
        for r in rows_only:
            partners |= by_work.get(r.openalex_id, set())
        partners.discard(rid)
        top_subfield = None
        subfields = [r.subfield for r in rows_only if r.subfield]
        if subfields:
            counts = collections.Counter(subfields)
            best = max(counts.values())
            top_subfield = min(k for k, v in counts.items() if v == best)
        session.add(ResearcherMetrics(
            researcher_id=rid,
            works_count_3y=n_works,
            total_citations=sum(r.cited_by_count for r in rows_only),
            fwci_mean=round(statistics.mean(fwcis), 4) if fwcis else None,
            fwci_median=round(statistics.median(fwcis), 4) if fwcis else None,
            fwci_total=round(sum(fwcis), 4) if fwcis else 0,
            top10pct_count=sum(1 for r in rows_only if r.is_top10pct),
            top1pct_count=sum(1 for r in rows_only if r.is_top1pct),
            first_author_count=sum(1 for it in items if it["first"]),
            corresponding_count=sum(1 for it in items if it["corresponding"]),
            fractional_works=round(sum(1 / d for d in divisors), 4),
            fractional_citations=round(
                sum(r.cited_by_count / d for r, d in zip(countable, divisors)), 4),
            avg_authors=round(statistics.mean(divisors), 4) if countable else None,
            intl_collab_rate=_rate(
                sum(1 for r in rows_only if r.is_intl_collab), n_works),
            corp_collab_rate=_rate(
                sum(1 for r in rows_only if r.is_corp_collab), n_works),
            oa_rate=_rate(sum(1 for r in rows_only if r.is_oa), n_works),
            preprint_count=sum(1 for r in rows_only if r.type == "preprint"),
            dataset_software_count=sum(
                1 for r in rows_only if r.type in ("dataset", "software")),
            unique_coauthors=len(partners),
            top_subfield=top_subfield,
            kaken_pi_count=sum(
                1 for k in kaken_by_author.get(rid, [])
                if k.role == "principal"),
            kaken_copi_count=sum(
                1 for k in kaken_by_author.get(rid, [])
                if k.role == "co_investigator"),
            kaken_total_amount=sum(
                k.total_amount for k in kaken_by_author.get(rid, [])
                if k.role == "principal"),
            awards_count=ach_by_author.get(rid, {}).get("award", 0),
            books_count=ach_by_author.get(rid, {}).get("book", 0),
            presentations_count=ach_by_author.get(rid, {}).get(
                "presentation", 0),
            committee_count=ach_by_author.get(rid, {}).get("committee", 0),
            computed_at=today.isoformat(),
        ))
        n += 1
    session.commit()
    return n
