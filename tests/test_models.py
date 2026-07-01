from sqlalchemy.orm import Session

from db.models import Researcher, Work, Authorship, get_engine


def test_roundtrip_in_memory():
    engine = get_engine(":memory:")
    with Session(engine) as s:
        s.add(Researcher(
            openalex_id="A123", display_name="Taro Yamada", orcid="0000-0001-2345-6789",
            h_index=10, works_count=50, raw_json="{}", updated_at="2026-07-02",
        ))
        s.add(Work(
            openalex_id="W456", doi="10.1000/x", title="t", publication_date="2024-01-01",
            venue="J", type="article", cited_by_count=3, fwci=1.5, cnp_value=0.9,
            is_top1pct=False, is_top10pct=True, topic="AI", subfield="ML",
            is_oa=True, raw_json="{}", updated_at="2026-07-02",
        ))
        s.add(Authorship(work_id="W456", author_id="A123",
                         author_position="first", is_corresponding=True))
        s.commit()
        assert s.get(Researcher, "A123").display_name == "Taro Yamada"
        assert s.get(Work, "W456").is_top10pct is True
        assert s.get(Authorship, ("W456", "A123")).author_position == "first"
