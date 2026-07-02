from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Researcher(Base):
    __tablename__ = "researchers"
    openalex_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    orcid: Mapped[str | None] = mapped_column(String, nullable=True)
    h_index: Mapped[int] = mapped_column(Integer, default=0)
    works_count: Mapped[int] = mapped_column(Integer, default=0)
    i10_index: Mapped[int] = mapped_column(Integer, default=0)
    two_yr_mean_citedness: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    # Phase 2 (公式名簿) で埋める列。Phase 1 では NULL のまま
    name_ja: Mapped[str | None] = mapped_column(String, nullable=True)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    is_official_roster: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class Work(Base):
    __tablename__ = "works"
    openalex_id: Mapped[str] = mapped_column(String, primary_key=True)
    doi: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[str] = mapped_column(String, index=True)
    venue: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    cited_by_count: Mapped[int] = mapped_column(Integer, default=0)
    fwci: Mapped[float | None] = mapped_column(Float, nullable=True)
    cnp_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_top1pct: Mapped[bool] = mapped_column(Boolean, default=False)
    is_top10pct: Mapped[bool] = mapped_column(Boolean, default=False)
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    subfield: Mapped[str | None] = mapped_column(String, nullable=True)
    is_oa: Mapped[bool] = mapped_column(Boolean, default=False)
    n_authors: Mapped[int] = mapped_column(Integer, default=0)
    is_intl_collab: Mapped[bool] = mapped_column(Boolean, default=False)
    is_corp_collab: Mapped[bool] = mapped_column(Boolean, default=False)
    is_authors_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String)


class Authorship(Base):
    __tablename__ = "authorships"
    work_id: Mapped[str] = mapped_column(String, primary_key=True)
    author_id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    author_position: Mapped[str | None] = mapped_column(String, nullable=True)
    is_corresponding: Mapped[bool] = mapped_column(Boolean, default=False)


class ResearcherMetrics(Base):
    __tablename__ = "researcher_metrics"
    researcher_id: Mapped[str] = mapped_column(String, primary_key=True)
    works_count_3y: Mapped[int] = mapped_column(Integer, default=0)
    total_citations: Mapped[int] = mapped_column(Integer, default=0)
    fwci_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    fwci_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    top10pct_count: Mapped[int] = mapped_column(Integer, default=0)
    first_author_count: Mapped[int] = mapped_column(Integer, default=0)
    corresponding_count: Mapped[int] = mapped_column(Integer, default=0)
    top1pct_count: Mapped[int] = mapped_column(Integer, default=0)
    fractional_works: Mapped[float | None] = mapped_column(Float, nullable=True)
    fractional_citations: Mapped[float | None] = mapped_column(
        Float, nullable=True)
    avg_authors: Mapped[float | None] = mapped_column(Float, nullable=True)
    intl_collab_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    corp_collab_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    oa_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    preprint_count: Mapped[int] = mapped_column(Integer, default=0)
    dataset_software_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_coauthors: Mapped[int] = mapped_column(Integer, default=0)
    top_subfield: Mapped[str | None] = mapped_column(String, nullable=True)
    computed_at: Mapped[str] = mapped_column(String)


class SyncState(Base):
    __tablename__ = "sync_state"
    source: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str | None] = mapped_column(String, nullable=True)
    last_synced_at: Mapped[str | None] = mapped_column(String, nullable=True)


def get_engine(path: str = "db/researchers.db"):
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
    return engine
