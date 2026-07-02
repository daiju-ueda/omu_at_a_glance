import datetime
import logging
from collections import defaultdict

from sqlalchemy import select

from collector.nameutil import normalize_name
from collector.sync import window_start
from db.models import Authorship, Researcher, Work

logger = logging.getLogger(__name__)

COAUTHOR_OVERLAP_MIN = 2


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def apply_dedup(session, today: datetime.date | None = None) -> int:
    members_all = session.execute(
        select(Researcher.openalex_id, Researcher.display_name,
               Researcher.orcid, Researcher.works_count)).all()
    groups: dict[str, list] = defaultdict(list)
    for m in members_all:
        groups[normalize_name(m.display_name)].append(m)

    start = window_start(today or datetime.date.today())
    works_by_author: dict[str, set[str]] = defaultdict(set)
    authors_by_work: dict[str, set[str]] = defaultdict(set)
    for work_id, author_id in session.execute(
            select(Authorship.work_id, Authorship.author_id)
            .join(Work, Work.openalex_id == Authorship.work_id)
            .where(Work.publication_date >= start)):
        works_by_author[author_id].add(work_id)
        authors_by_work[work_id].add(author_id)

    coauthor_cache: dict[str, set[str]] = {}

    def coauthors(aid: str) -> set[str]:
        if aid not in coauthor_cache:
            out: set[str] = set()
            for w in works_by_author.get(aid, set()):
                out |= authors_by_work[w]
            out.discard(aid)
            coauthor_cache[aid] = out
        return coauthor_cache[aid]

    canonical_map: dict[str, str] = {}
    for name, members in groups.items():
        if len(members) < 2:
            continue
        ids = [m.openalex_id for m in members]
        orcid_of = {m.openalex_id: m.orcid for m in members}
        uf = _UnionFind(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                oa, ob = orcid_of[a], orcid_of[b]
                if oa and ob and oa != ob:
                    continue  # 分離証拠が最優先
                if (oa and ob and oa == ob) \
                        or (works_by_author[a] & works_by_author[b]) \
                        or len(coauthors(a) & coauthors(b)) >= COAUTHOR_OVERLAP_MIN:
                    uf.union(a, b)
        clusters: dict[str, list] = defaultdict(list)
        for m in members:
            clusters[uf.find(m.openalex_id)].append(m)
        for cluster in clusters.values():
            if len(cluster) < 2:
                continue
            orcids = {m.orcid for m in cluster if m.orcid}
            if len(orcids) > 1:
                logger.warning(
                    "dedup: 異なるORCIDを含むクラスタを解散 (%s, %d人)",
                    name, len(cluster))
                continue
            canonical = sorted(
                cluster, key=lambda m: (-m.works_count, m.openalex_id))[0]
            for m in cluster:
                if m.openalex_id != canonical.openalex_id:
                    canonical_map[m.openalex_id] = canonical.openalex_id

    # 全再計算・冪等: 全行のcanonical_idを更新
    for researcher in session.scalars(select(Researcher)):
        researcher.canonical_id = canonical_map.get(researcher.openalex_id)

    # エイリアス属性の引き継ぎ（正準未設定時のみ）とクリア
    for alias_id, canon_id in canonical_map.items():
        alias = session.get(Researcher, alias_id)
        canon = session.get(Researcher, canon_id)
        if alias is None or canon is None:
            continue
        if alias.name_ja and not canon.name_ja:
            canon.name_ja = alias.name_ja
        if alias.department and not canon.department:
            canon.department = alias.department
            canon.position = alias.position
            canon.is_official_roster = alias.is_official_roster
        alias.name_ja = None
        alias.department = None
        alias.position = None
        alias.is_official_roster = False
    session.commit()
    logger.info("dedup: %d件をエイリアス化", len(canonical_map))
    return len(canonical_map)
