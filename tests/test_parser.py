from __future__ import annotations

import asyncio
import time

from backend.db.models import PageChunk, RawPage
from backend.db.store import Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.parser import (
    Chunk,
    ChunkEventEmitter,
    ChunkExtraction,
    ExtractedClaim,
    ExtractedConnection,
    ExtractedFact,
    ExtractedGraphProject,
    ExtractedOrganization,
    ExtractedPerson,
    ExtractedPosition,
    ExtractedProfile,
    ExtractedProject,
    ExtractedRelationship,
    ExtractionClient,
    ItemVerdict,
    MockSynthesisClient,
    MockValidationClient,
    PageExtraction,
    Parser,
    SynthesisClient,
    SynthesizedProfile,
    ValidationClient,
    ValidationResult,
    chunk_page,
    page_extraction_from_chunks,
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
                    subject_name="Jane Doe",
                    connected_name="Pat Person",
                    context="Worked together at Acme.",
                    confidence_score=0.8,
                    text_evidence="Worked together at Acme.",
                )
            ],
            projects=[
                ExtractedProject(
                    subject_name="Jane Doe",
                    project_name="Project Pine",
                    description="A project.",
                    confidence_score=0.7,
                    text_evidence="Project Pine",
                )
            ],
            facts=[
                ExtractedFact(
                    category="career",
                    content="Jane Doe is COO at Acme Corp.",
                    confidence="high",
                    confidence_score=0.9,
                    text_evidence="Jane Doe is COO at Acme Corp.",
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


class SlowAsyncExtractionClient(ExtractionClient):
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def extract_page_async(
        self,
        raw_page: RawPage,
        chunks: list[Chunk],
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> PageExtraction:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay_seconds)
            if emit_chunk_event is not None:
                for chunk in chunks:
                    emit_chunk_event("chunk_done", raw_page, chunk, {}, True)
            return PageExtraction(
                profile=ExtractedProfile(
                    bio_summary=f"{raw_page.alum_name} was parsed concurrently."
                )
            )
        finally:
            self.active -= 1

    def extract(self, raw_page: RawPage) -> PageExtraction:
        raise AssertionError(f"sync extract should not run for {raw_page.source_url}")


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
    assert page.entity_id is not None
    assert extractor.calls == 1
    assert synthesizer.calls == 1
    assert store.list_connections()[0].source_raw_page_id == page.id
    assert store.list_connections()[0].entity_id == page.entity_id
    assert store.list_connections()[0].validation_verdict == "keep"
    assert store.list_connections()[0].confidence_score == 0.8
    assert store.list_connections()[0].text_evidence == "Worked together at Acme."
    assert store.list_projects()[0].source_raw_page_id == page.id
    assert store.list_projects()[0].entity_id == page.entity_id
    assert store.list_projects()[0].validation_verdict == "uncertain"
    assert store.list_projects()[0].confidence_score == 0.7
    assert store.list_projects()[0].text_evidence == "Project Pine"
    assert store.list_facts()[0].source_raw_page_id == page.id
    assert store.list_facts()[0].entity_id == page.entity_id
    assert store.list_facts()[0].validation_verdict == "drop"
    assert store.list_facts()[0].confidence_score == 0.9
    assert store.list_facts()[0].text_evidence == "Jane Doe is COO at Acme Corp."
    assert store.list_profiles()[0].current_company == "Acme Corp"
    with store.session() as session:
        assert session.query(PageChunk).count() == 1
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
    assert len(store.list_connections()) == 2
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


def test_parser_processes_mock_pages_concurrently_with_chunk_progress(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PARSE_CONCURRENCY", "8")
    store = Store(f"sqlite:///{tmp_path / 'parallel-parse.db'}")
    store.init_db()
    for index in range(50):
        name = f"Person {index}"
        store.upsert_profile(name=name, class_year="T'24")
        store.save_raw_page(
            alum_name=name,
            source_url=f"https://example.com/person-{index}",
            page_title=name,
            page_text=f"{name} works at Acme Corp.",
        )
    extractor = SlowAsyncExtractionClient(delay_seconds=0.05)
    parser = Parser(
        store=store,
        extractor=extractor,
        validator=MockValidationClient(),
        synthesizer=MockSynthesisClient(),
    )
    events: list[ProgressEvent] = []

    started_at = time.perf_counter()
    parser.run(events.append)
    elapsed_seconds = time.perf_counter() - started_at

    assert elapsed_seconds < 5.0
    assert extractor.calls == 50
    assert extractor.max_active > 1
    chunk_events = [event for event in events if event.kind == "chunk_done"]
    assert len(chunk_events) == 50
    assert chunk_events[-1].data["overall_total"] == 50
    assert chunk_events[-1].data["overall_done"] == 50


def test_parser_filter_keywords_only_marks_matching_unparsed_pages(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'filtered-parse.db'}")
    store.init_db()
    pages = [
        ("Errik Anderson", "https://example.com/gyrobike", "Gyrobike first-year project"),
        ("Jane Doe", "https://example.com/jane", "General alumni profile"),
        ("Pat Person", "https://example.com/fyp", "First-year project evidence"),
    ]
    for name, url, text in pages:
        store.upsert_profile(name=name, class_year="T'24")
        store.save_raw_page(alum_name=name, source_url=url, page_title=name, page_text=text)
    extractor = FakeExtractionClient()
    parser = Parser(
        store=store,
        extractor=extractor,
        validator=MockValidationClient(),
        synthesizer=MockSynthesisClient(),
    )

    parser.run(lambda event: None, keywords=["gyrobike", "first-year project"])

    pages_by_url = {page.source_url: page for page in store.list_raw_pages()}
    assert pages_by_url["https://example.com/gyrobike"].parsed_at is not None
    assert pages_by_url["https://example.com/fyp"].parsed_at is not None
    assert pages_by_url["https://example.com/jane"].parsed_at is None
    assert extractor.calls == 2


def test_parser_does_not_attach_other_people_claims_to_page_entity(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'claim-attribution.db'}")
    store.init_db()
    errik = store.upsert_profile(name="Errik Anderson", class_year="T'07")
    store.save_raw_page(
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        source_url="https://example.com/eir",
        page_title="Entrepreneur in residence",
        page_text=(
            "Daniella Reichstetter T'07 became founder and CEO of Gyrobike. "
            "Errik Anderson T'07 advised Gyrobike. "
            "Phillips came to Tuck from the University of Southern California."
        ),
    )

    class MultiPersonExtractionClient(ExtractionClient):
        def extract(self, raw_page: RawPage) -> PageExtraction:
            del raw_page
            return PageExtraction(
                claims=[
                    ExtractedClaim(
                        subject_name="Daniella Reichstetter",
                        subject_context="T'07 founder",
                        predicate="founded",
                        object_name="Gyrobike",
                        object_type="organization",
                        text_evidence=(
                            "Daniella Reichstetter T'07 became founder and CEO of Gyrobike."
                        ),
                        confidence=0.95,
                    ),
                    ExtractedClaim(
                        subject_name="Errik Anderson",
                        subject_context="T'07 advisor",
                        predicate="advised",
                        object_name="Gyrobike",
                        object_type="organization",
                        text_evidence="Errik Anderson T'07 advised Gyrobike.",
                        confidence=0.9,
                    ),
                    ExtractedClaim(
                        subject_name="Phillips",
                        predicate="educated_at",
                        object_value="University of Southern California",
                        object_type="education",
                        text_evidence=(
                            "Phillips came to Tuck from the University of Southern California."
                        ),
                        confidence=0.9,
                    ),
                ]
            )

    parser = Parser(
        store=store,
        extractor=MultiPersonExtractionClient(),
        validator=MockValidationClient(),
        synthesizer=MockSynthesisClient(),
    )

    parser.run(lambda event: None)

    connections = store.list_connections(("keep",))
    edge_tuples = {
        (connection.alum_name, connection.relationship_type, connection.connected_name)
        for connection in connections
    }
    assert ("Errik Anderson", "advised", "Gyrobike") in edge_tuples
    assert ("Daniella Reichstetter", "founded", "Gyrobike") in edge_tuples
    assert ("Errik Anderson", "founded", "Gyrobike") not in edge_tuples
    assert all(connection.connected_entity_id is not None for connection in connections)

    detail = store.entity_detail(errik.entity_id)
    assert detail is not None
    assert [rel["relationship_type"] for rel in detail["relationships"]] == ["advised"]
    assert store.list_claims(("keep",))[0].subject_name == "Daniella Reichstetter"


def test_chunk_page_returns_one_chunk_for_short_text() -> None:
    chunks = chunk_page("Jane Doe worked on Gyrobike.", target_tokens=4000, overlap_tokens=200)

    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len("Jane Doe worked on Gyrobike.")
    assert chunks[0].text == "Jane Doe worked on Gyrobike."


def test_chunk_page_overlaps_long_text() -> None:
    text = " ".join(f"token{i}" for i in range(500))

    chunks = chunk_page(text, target_tokens=80, overlap_tokens=10)

    assert len(chunks) > 1
    assert chunks[0].char_start == 0
    assert chunks[1].char_start < chunks[0].char_end


def test_page_extraction_from_unified_chunk_shape_maps_to_structured_rows() -> None:
    raw_page = RawPage(
        id=1,
        alum_name="Errik Anderson",
        source_url="https://example.com",
        page_title="Example",
        page_text="Errik Anderson partnered with Gyrobike.",
        fetched_at=None,  # type: ignore[arg-type]
    )
    extraction = page_extraction_from_chunks(
        raw_page,
        [
            ChunkExtraction(
                people=[
                    ExtractedPerson(
                        name="Errik Anderson",
                        text_evidence="Errik Anderson partnered with Gyrobike.",
                        confidence=0.95,
                    )
                ],
                organizations=[
                    ExtractedOrganization(
                        name="Gyrobike",
                        text_evidence="partnered with Gyrobike",
                        confidence=0.9,
                    )
                ],
                relationships=[
                    ExtractedRelationship(
                        source_name="Errik Anderson",
                        target_name="Gyrobike",
                        relationship_type="partnered_with",
                        context="Errik Anderson partnered with Gyrobike.",
                        text_evidence="Errik Anderson partnered with Gyrobike.",
                        confidence=0.88,
                    )
                ],
                projects=[
                    ExtractedGraphProject(
                        project_name="Gyrobike",
                        description="Bike training project.",
                        people=["Errik Anderson"],
                        organizations=["Gyrobike"],
                        text_evidence="Gyrobike",
                        confidence=0.9,
                    )
                ],
            )
        ],
    )

    assert not extraction.facts
    assert extraction.claims
    assert extraction.claims[0].subject_name == "Errik Anderson"
    assert extraction.claims[0].object_name == "Gyrobike"
    assert extraction.connections[0].connected_name == "Gyrobike"
    assert extraction.connections[0].confidence_score == 0.88
    assert extraction.connections[0].text_evidence == "Errik Anderson partnered with Gyrobike."
    assert any(
        connection.connected_name == "Gyrobike"
        and connection.relationship_type == "worked_on_project"
        for connection in extraction.connections
    )
    assert extraction.projects[0].project_name == "Gyrobike"
    assert extraction.projects[0].subject_name == "Errik Anderson"
    assert extraction.projects[0].text_evidence == "Gyrobike"
