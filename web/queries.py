import datetime

from sqlalchemy import func, or_, select

from collector.sync import window_start
from db.models import (Authorship, Researcher, ResearcherMetrics, SyncState,
                       Work)

PAGE_SIZE = 100
MIN_DEPT_MEMBERS = 5  # 部局間per-capita比較の順位対象とする最低名寄せ人数

SORT_COLUMNS = {
    "fwci_mean": ResearcherMetrics.fwci_mean,
    "total_citations": ResearcherMetrics.total_citations,
    "top10pct_count": ResearcherMetrics.top10pct_count,
    "works_count_3y": ResearcherMetrics.works_count_3y,
    "fractional_citations": ResearcherMetrics.fractional_citations,
    "kaken_total_amount": ResearcherMetrics.kaken_total_amount,
    "fwci_total": ResearcherMetrics.fwci_total,
}


def ranking(session, sort="fwci_total", min_works=1, page=1, department=None):
    col = SORT_COLUMNS.get(sort, ResearcherMetrics.fwci_total)
    count_q = (select(func.count())
               .select_from(ResearcherMetrics)
               .join(Researcher,
                     Researcher.openalex_id == ResearcherMetrics.researcher_id))
    rows_q = (select(Researcher, ResearcherMetrics)
              .join(ResearcherMetrics,
                    ResearcherMetrics.researcher_id == Researcher.openalex_id))
    if department:
        count_q = count_q.where(Researcher.department == department)
        rows_q = rows_q.where(Researcher.department == department)
    total_all = session.scalar(count_q)
    cond = ResearcherMetrics.works_count_3y >= min_works
    total = session.scalar(count_q.where(cond))
    rows = session.execute(
        rows_q.where(cond)
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
    # Fetch works for both canonical and aliases, deduplicating by work_id
    author_ids = [openalex_id] + list(session.scalars(
        select(Researcher.openalex_id)
        .where(Researcher.canonical_id == openalex_id)))
    raw_works = session.execute(
        select(Work, Authorship)
        .join(Authorship, Authorship.work_id == Work.openalex_id)
        .where(Authorship.author_id.in_(author_ids),
               Work.publication_date >= start)
        .order_by(Work.cited_by_count.desc(), Work.openalex_id)
    ).all()
    # 同一workに正準・エイリアス両方の著者行がある場合は
    # metricsのOR集計に合わせて first > corresponding を優先表示
    best_by_work: dict[str, object] = {}
    order: list[str] = []
    for row in raw_works:
        work_id = row.Work.openalex_id
        if work_id not in best_by_work:
            best_by_work[work_id] = row
            order.append(work_id)
            continue
        current = best_by_work[work_id]
        def _rank(r):
            return (r.Authorship.author_position == "first",
                    bool(r.Authorship.is_corresponding))
        if _rank(row) > _rank(current):
            best_by_work[work_id] = row
    works = [best_by_work[w] for w in order]
    return researcher, metrics, works


def search(session, q, limit=200):
    pattern = f"%{q}%"
    return session.execute(
        select(Researcher, ResearcherMetrics)
        .outerjoin(ResearcherMetrics,
                   ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(Researcher.canonical_id.is_(None),
               or_(Researcher.display_name.ilike(pattern),
                   Researcher.name_ja.ilike(pattern)))
        .order_by(ResearcherMetrics.fwci_total.desc(), Researcher.openalex_id)
        .limit(limit)
    ).all()


RANK_METRICS = {
    "works_count_3y": ResearcherMetrics.works_count_3y,
    "total_citations": ResearcherMetrics.total_citations,
    "fractional_citations": ResearcherMetrics.fractional_citations,
    "fwci_total": ResearcherMetrics.fwci_total,
    "fwci_mean": ResearcherMetrics.fwci_mean,
    "top10pct_count": ResearcherMetrics.top10pct_count,
    "top1pct_count": ResearcherMetrics.top1pct_count,
    "kaken_total_amount": ResearcherMetrics.kaken_total_amount,
}


def metric_ranks(session, researcher_id):
    own = session.get(ResearcherMetrics, researcher_id)
    if own is None or own.works_count_3y < 1:
        return None
    population = ResearcherMetrics.works_count_3y >= 1
    total = session.scalar(
        select(func.count()).select_from(ResearcherMetrics).where(population))
    ranks: dict[str, tuple[int, int]] = {}
    for key, col in RANK_METRICS.items():
        value = getattr(own, key)
        if value is None or value == 0:
            continue
        higher = session.scalar(
            select(func.count()).select_from(ResearcherMetrics)
            .where(population, col > value))
        ranks[key] = (higher + 1, total)
    return ranks


def compare(session, ids):
    if not ids:
        return []
    rows = session.execute(
        select(Researcher, ResearcherMetrics)
        .outerjoin(ResearcherMetrics,
                   ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(Researcher.openalex_id.in_(ids))
    ).all()
    by_id = {row.Researcher.openalex_id: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


def last_synced(session):
    state = session.get(SyncState, "works")
    return state.last_synced_at if state else None


def departments_list(session):
    return list(session.scalars(
        select(Researcher.department)
        .where(Researcher.department.is_not(None))
        .distinct()
        .order_by(Researcher.department)))


def department_stats(session):
    rows = session.execute(
        select(Researcher.department,
               func.count(),
               func.sum(ResearcherMetrics.works_count_3y),
               func.sum(ResearcherMetrics.total_citations),
               func.sum(ResearcherMetrics.fwci_total),
               func.sum(ResearcherMetrics.top10pct_count),
               func.sum(ResearcherMetrics.kaken_total_amount))
        .join(ResearcherMetrics,
              ResearcherMetrics.researcher_id == Researcher.openalex_id)
        .where(Researcher.department.is_not(None))
        .group_by(Researcher.department)
    ).all()
    stats = []
    for dept, n, works, cites, fwci, top10, kaken in rows:
        stats.append({
            "department": dept,
            "members": n,
            "works": works or 0,
            "citations": cites or 0,
            "fwci_total": round(fwci or 0, 2),
            "works_per_capita": round((works or 0) / n, 2),
            "fwci_per_capita": round((fwci or 0) / n, 2),
            "top10": top10 or 0,
            "kaken_amount": kaken or 0,
        })
    # 名寄せ人数が少ない部局はper-capita値が極端に振れるため順位対象から除外し、
    # 参考表示（人数降順）に回す（n=1の部局が首位に立つ問題への対処）
    ranked = [s for s in stats if s["members"] >= MIN_DEPT_MEMBERS]
    ranked.sort(key=lambda item: item["fwci_per_capita"], reverse=True)
    small = [s for s in stats if s["members"] < MIN_DEPT_MEMBERS]
    small.sort(key=lambda item: (-item["members"], item["department"]))
    return ranked, small
