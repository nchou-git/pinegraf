from __future__ import annotations

from backend.db.models import RawPage
from backend.db.store import Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.parser import (
    ExtractedConnection,
    ExtractedFact,
    ExtractedPosition,
    ExtractedProfile,
    ExtractedProject,
    ExtractionClient,
    ItemVerdict,
    PageExtraction,
    Parser,
    SynthesisClient,
    SynthesizedProfile,
    ValidationClient,
    ValidationResult,
)


class FakeExtractionClient(ExtractionClient):
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, raw_page: RawPage) -> PageExtraction:
        self.calls += 1
        return PageExtraction(
            profile=ExtractedProfile(
                current_company="Acme Corp",
                current_title="COO",
                past_companies=["Beta Inc"],
                education=["Dartmouth Tuck MBA"],
                bio_summary=f"{raw_page.alum_name} has a parsed profile.",
            ),
            connections=[
                ExtractedConnection(
                    connected_name="Pat Person",
                    context="Worked together at Acme.",
                )
            ],
            projects=[ExtractedProject(project_name="Project Pine", description="A project.")],
            facts=[
                ExtractedFact(
                    category="career",
                    content="Jane Doe is COO at Acme Corp.",
                    confidence="high",
                )
            ],
            positions=[
                ExtractedPosition(
                    company="Acme Corp",
                    title="COO",
                    location="Boston, MA",
                    start_date="2024-06",
                    end_date=None,
                    position_type="full_time",
                ),
                ExtractedPosition(
                    company="River Ventures",
                    title="Board Member",
                    location=None,
                    start_date="2023",
                    end_date=None,
                    position_type="board",
                ),
                ExtractedPosition(
                    company="Beta Inc",
                    title="VP Strategy",
                    location=None,
                    start_date="2021-01",
                    end_date="2024-05",
                    position_type="full_time",
                ),
            ],
        )


class FakeValidationClient(ValidationClient):
    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        del raw_page, extraction
        return ValidationResult(
            connection_verdicts=[ItemVerdict(index=0, verdict="keep")],
            project_verdicts=[ItemVerdict(index=0, verdict="uncertain")],
            fact_verdicts=[ItemVerdict(index=0, verdict="drop")],
            position_verdicts=[
                ItemVerdict(index=0, verdict="keep"),
                ItemVerdict(index=1, verdict="keep"),
                ItemVerdict(index=2, verdict="keep"),
            ],
        )


class FakeSynthesisClient(SynthesisClient):
    def __init__(self) -> None:
        self.calls = 0

    def synthesize(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        self.calls += 1
        assert alum_name == "Jane Doe"
        assert class_year == "T'24"
        assert "Project Pine" in str(evidence)
        return SynthesizedProfile(
            current_company="Acme Corp",
            current_title="COO",
            past_companies=["Beta Inc"],
            education=["Dartmouth Tuck MBA"],
            bio_summary="Synthesized profile.",
        )


def make_parser(tmp_path) -> tuple[Store, FakeExtractionClient, FakeSynthesisClient, Parser]:
    store = Store(f"sqlite:///{tmp_path / 'parse.db'}")
    store.init_db()
    store.upsert_profile(name="Jane Doe", class_year="T'24")
    store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe is COO at Acme Corp.",
    )
    extractor = FakeExtractionClient()
    synthesizer = FakeSynthesisClient()
    parser = Parser(
        store=store,
        extractor=extractor,
        validator=FakeValidationClient(),
        synthesizer=synthesizer,
    )
    return store, extractor, synthesizer, parser


def test_parser_writes_structured_rows_marks_parsed_and_is_idempotent(tmp_path) -> None:
    store, extractor, synthesizer, parser = make_parser(tmp_path)
    events: list[ProgressEvent] = []

    parser.run(events.append)

    page = store.list_raw_pages()[0]
    assert page.parsed_at is not None
    assert extractor.calls == 1
    assert synthesizer.calls == 1
    assert store.list_connections()[0].source_raw_page_id == page.id
    assert store.list_connections()[0].validation_verdict == "keep"
    assert store.list_projects()[0].source_raw_page_id == page.id
    assert store.list_projects()[0].validation_verdict == "uncertain"
    assert store.list_facts()[0].source_raw_page_id == page.id
    assert store.list_facts()[0].validation_verdict == "drop"
    assert store.list_profiles()[0].current_company == "Acme Corp"
    assert any(event.kind == "page_parsed" for event in events)

    parser.run(events.append)

    assert extractor.calls == 1
    assert synthesizer.calls == 1
    facts = store.list_facts()
    assert len([fact for fact in facts if fact.category == "career"]) == 1
    assert len([fact for fact in facts if fact.category == "position"]) == 3


def test_parser_force_reparses_without_duplicate_rows(tmp_path) -> None:
    store, extractor, synthesizer, parser = make_parser(tmp_path)
    parser.run(lambda event: None)

    parser.run(lambda event: None, force=True)

    assert extractor.calls == 2
    assert synthesizer.calls == 2
    facts = store.list_facts()
    assert len([fact for fact in facts if fact.category == "career"]) == 1
    assert len([fact for fact in facts if fact.category == "position"]) == 3
    assert len(store.list_connections()) == 1
    assert len(store.list_projects()) == 1


def test_parser_returns_multiple_positions_with_correct_type_and_currentness(tmp_path) -> None:
    store, _, _, parser = make_parser(tmp_path)

    parser.run(lambda event: None)

    positions = store.get_positions_for_alum("Jane Doe")
    assert len(positions) == 3
    assert positions[0]["position_type"] in {"full_time", "board"}
    assert positions[0]["is_current"] is True
    assert sorted(position["position_type"] for position in positions) == [
        "board",
        "full_time",
        "full_time",
    ]
    assert [position["is_current"] for position in positions].count(True) == 2
