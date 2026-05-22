from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator

from backend.db.models import RawPage
from backend.db.store import SYNTHESIS_VERDICTS, Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.openai_retry import retry_openai_call
from backend.resolution.entity_resolver import resolve_or_create

MAX_EXTRACTION_CHARS = 30_000
ValidationVerdict = Literal["keep", "uncertain", "drop"]


class ExtractedProfile(BaseModel):
    current_company: str = ""
    current_title: str = ""
    past_companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    bio_summary: str = ""


class ExtractedConnection(BaseModel):
    connected_name: str
    context: str = ""
    relationship_type: str = "associate"
    validation_verdict: ValidationVerdict = "keep"


class ExtractedProject(BaseModel):
    project_name: str
    description: str = ""
    validation_verdict: ValidationVerdict = "keep"


class ExtractedFact(BaseModel):
    category: str = "general"
    content: str
    confidence: str = "low"
    validation_verdict: ValidationVerdict = "keep"


PositionType = Literal["full_time", "advisor", "board", "founder", "consultant", "other"]


class ExtractedPosition(BaseModel):
    company: str
    title: str
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    position_type: PositionType = "other"
    is_current: bool = True
    confidence: str = "low"
    validation_verdict: ValidationVerdict = "keep"

    @model_validator(mode="after")
    def _derive_current_from_end_date(self) -> "ExtractedPosition":
        self.is_current = self.end_date is None
        return self


class PageExtraction(BaseModel):
    profile: ExtractedProfile = Field(default_factory=ExtractedProfile)
    connections: list[ExtractedConnection] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    positions: list[ExtractedPosition] = Field(default_factory=list)


class ItemVerdict(BaseModel):
    index: int
    verdict: ValidationVerdict
    reason: str = ""


class ValidationResult(BaseModel):
    connection_verdicts: list[ItemVerdict] = Field(default_factory=list)
    project_verdicts: list[ItemVerdict] = Field(default_factory=list)
    fact_verdicts: list[ItemVerdict] = Field(default_factory=list)
    position_verdicts: list[ItemVerdict] = Field(default_factory=list)


class SynthesizedProfile(BaseModel):
    current_company: str = ""
    current_title: str = ""
    past_companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    bio_summary: str = ""


class ExtractionClient:
    def extract(self, raw_page: RawPage) -> PageExtraction:
        raise NotImplementedError


class ValidationClient:
    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        raise NotImplementedError


class SynthesisClient:
    def synthesize(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        raise NotImplementedError


class OpenAIExtractionClient(ExtractionClient):
    def __init__(self, api_key: str, model: str = "gpt-5.4-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract(self, raw_page: RawPage) -> PageExtraction:
        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Extract structured information about the named alumnus from the "
                            "provided public page. Use only the page text. Return empty fields "
                            "for unsupported profile data. Connections must be direct "
                            "relationships involving this alumnus. Projects must be directly "
                            "attributed to this alumnus. Facts must be claims about this "
                            "alumnus personally. Positions must include all supported roles for "
                            "this alumnus, including concurrent roles (for example board seats "
                            "alongside a day job, advisor roles, or founder plus employment). "
                            "Use one position object per role. Set is_current=true iff end_date "
                            "is null. Do not include data about unrelated people with the same "
                            "name."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus: {raw_page.alum_name}\n"
                            f"Page URL: {raw_page.source_url}\n"
                            f"Page title: {raw_page.page_title}\n\n"
                            f"Page text:\n{raw_page.page_text[:MAX_EXTRACTION_CHARS]}"
                        ),
                    },
                ],
                text_format=PageExtraction,
            )
        )
        return response.output_parsed or PageExtraction()


class OpenAIValidationClient(ValidationClient):
    def __init__(self, api_key: str, model: str = "gpt-5.4-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        payload = {
            "connections": [
                connection.model_dump(exclude={"validation_verdict"})
                for connection in extraction.connections
            ],
            "projects": [
                project.model_dump(exclude={"validation_verdict"})
                for project in extraction.projects
            ],
            "facts": [fact.model_dump(exclude={"validation_verdict"}) for fact in extraction.facts],
            "positions": [
                position.model_dump(exclude={"validation_verdict"})
                for position in extraction.positions
            ],
        }
        if (
            not payload["connections"]
            and not payload["projects"]
            and not payload["facts"]
            and not payload["positions"]
        ):
            return ValidationResult()

        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "You validate extracted alumni evidence against the source page. "
                            "For every item, return a verdict: keep when directly supported, "
                            "uncertain when plausibly supported but weak or ambiguous, and drop "
                            "when unsupported, misattributed, or about someone else. Use the "
                            "same zero-based indices from the input arrays."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus: {raw_page.alum_name}\n"
                            f"Source URL: {raw_page.source_url}\n\n"
                            f"Page text:\n{raw_page.page_text[:MAX_EXTRACTION_CHARS]}\n\n"
                            f"Extracted items:\n{json.dumps(payload, indent=2)}"
                        ),
                    },
                ],
                text_format=ValidationResult,
            )
        )
        return response.output_parsed or ValidationResult()


class OpenAISynthesisClient(SynthesisClient):
    def __init__(self, api_key: str, model: str = "gpt-5.4") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def synthesize(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Synthesize one concise canonical alumni profile from validated "
                            "structured evidence. Use keep and uncertain evidence only. Do not "
                            "introduce facts that are absent from the evidence. Prefer specific "
                            "recent evidence, leave fields blank when evidence conflicts, and "
                            "keep the bio summary to two or three sentences."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus: {alum_name}\n"
                            f"Class year: {class_year}\n\n"
                            f"Evidence:\n{json.dumps(evidence, indent=2, default=str)}"
                        ),
                    },
                ],
                text_format=SynthesizedProfile,
            )
        )
        return response.output_parsed or SynthesizedProfile()


class MockExtractionClient(ExtractionClient):
    def extract(self, raw_page: RawPage) -> PageExtraction:
        lower = raw_page.page_text.lower()
        first_name = raw_page.alum_name.split()[0].lower()
        connections: list[ExtractedConnection] = []
        projects: list[ExtractedProject] = []

        if "gyrobike" in lower and first_name in {"errik", "daniella"}:
            connected_name = "Daniella Reichstetter" if first_name == "errik" else "Errik Anderson"
            connections.append(
                ExtractedConnection(
                    connected_name=connected_name,
                    context="Worked together on the Gyrobike first-year project at Tuck.",
                    relationship_type="project collaborator",
                )
            )
            projects.append(
                ExtractedProject(
                    project_name="Gyrobike FYP",
                    description="Tuck first-year project involving gyrobike work.",
                )
            )

        return PageExtraction(
            profile=ExtractedProfile(
                current_company="Acme Corp" if "acme" in lower else "",
                current_title="Senior Manager" if "senior manager" in lower else "",
                past_companies=["Beta Inc", "Gamma LLC"] if "beta" in lower else [],
                education=["Dartmouth Tuck MBA"] if "tuck" in lower else [],
                bio_summary=(
                    f"{raw_page.alum_name} has stored public-page evidence from Pinegraf's "
                    "mock parser."
                ),
            ),
            connections=connections,
            projects=projects,
            facts=[
                ExtractedFact(
                    category="career",
                    content=f"{raw_page.alum_name} is described in a public page.",
                    confidence="medium",
                )
            ],
            positions=[],
        )


class MockValidationClient(ValidationClient):
    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        del raw_page
        return ValidationResult(
            connection_verdicts=[
                ItemVerdict(index=index, verdict=connection.validation_verdict)
                for index, connection in enumerate(extraction.connections)
            ],
            project_verdicts=[
                ItemVerdict(index=index, verdict=project.validation_verdict)
                for index, project in enumerate(extraction.projects)
            ],
            fact_verdicts=[
                ItemVerdict(index=index, verdict=fact.validation_verdict)
                for index, fact in enumerate(extraction.facts)
            ],
        )


class MockSynthesisClient(SynthesisClient):
    def synthesize(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        del class_year
        page_profiles = evidence.get("page_profiles", [])
        profiles = [profile for profile in page_profiles if isinstance(profile, dict)]
        return SynthesizedProfile(
            current_company=first_nonempty(
                profile.get("current_company", "") for profile in profiles
            ),
            current_title=first_nonempty(profile.get("current_title", "") for profile in profiles),
            past_companies=dedupe_strings(iter_profile_list_values(profiles, "past_companies")),
            education=dedupe_strings(iter_profile_list_values(profiles, "education")),
            bio_summary=first_nonempty(profile.get("bio_summary", "") for profile in profiles)
            or f"{alum_name} has parsed public-page evidence in Pinegraf.",
        )


class Parser:
    def __init__(
        self,
        *,
        store: Store,
        extractor: ExtractionClient,
        validator: ValidationClient,
        synthesizer: SynthesisClient,
    ) -> None:
        self.store = store
        self.extractor = extractor
        self.validator = validator
        self.synthesizer = synthesizer

    def run(self, emit: Callable[[ProgressEvent], None], *, force: bool = False) -> None:
        pages = self.store.list_pages_to_parse(force=force)
        total_pages = len(pages)
        parsed_pages = 0
        emit(
            ProgressEvent(
                "parse_start",
                {"page_total": total_pages, "page_done": parsed_pages, "force": force},
            )
        )

        pages_by_alum: dict[tuple[str, uuid.UUID], list[RawPage]] = defaultdict(list)
        for page in pages:
            entity_id = self._entity_id_for_raw_page(page)
            pages_by_alum[(page.alum_name, entity_id)].append(page)

        for alum_index, ((alum_name, entity_id), alum_pages) in enumerate(
            pages_by_alum.items(), start=1
        ):
            emit(
                ProgressEvent(
                    "alum_start",
                    {
                        "name": alum_name,
                        "alum_index": alum_index,
                        "alum_total": len(pages_by_alum),
                        "page_total": len(alum_pages),
                    },
                )
            )
            page_profiles: list[ExtractedProfile] = []
            for page_index, raw_page in enumerate(alum_pages, start=1):
                extraction = self.extractor.extract(raw_page)
                validation = self.validator.validate(raw_page, extraction)
                apply_validation(extraction, validation)
                self.store.replace_structured_items(
                    raw_page_id=raw_page.id,
                    alum_name=raw_page.alum_name,
                    entity_id=entity_id,
                    facts=[
                        *[fact.model_dump() for fact in extraction.facts],
                        *[
                            {
                                "category": "position",
                                "content": json.dumps(
                                    {
                                        "company": position.company,
                                        "title": position.title,
                                        "location": position.location,
                                        "start_date": position.start_date,
                                        "end_date": position.end_date,
                                        "position_type": position.position_type,
                                        "is_current": position.is_current,
                                    }
                                ),
                                "confidence": position.confidence,
                                "validation_verdict": position.validation_verdict,
                            }
                            for position in extraction.positions
                        ],
                    ],
                    connections=[connection.model_dump() for connection in extraction.connections],
                    projects=[project.model_dump() for project in extraction.projects],
                )
                self.store.replace_entity_attributes(
                    entity_id=entity_id,
                    source_url=raw_page.source_url,
                    attributes=extracted_profile_attributes(extraction.profile),
                )
                self.store.mark_raw_page_parsed(raw_page.id)
                page_profiles.append(extraction.profile)
                parsed_pages += 1
                emit(
                    ProgressEvent(
                        "page_parsed",
                        {
                            "name": alum_name,
                            "raw_page_id": raw_page.id,
                            "url": raw_page.source_url,
                            "page_index": page_index,
                            "page_total": len(alum_pages),
                            "page_done": parsed_pages,
                            "overall_total": total_pages,
                            "overall_done": parsed_pages,
                            "verdict_counts": verdict_counts(extraction),
                        },
                    )
                )

            profile = self._synthesize_alum(alum_name, entity_id, page_profiles)
            emit(
                ProgressEvent(
                    "alum_done",
                    {
                        "name": alum_name,
                        "page_total": len(alum_pages),
                        "overall_total": total_pages,
                        "overall_done": parsed_pages,
                        "current_company": profile.current_company,
                    },
                )
            )

        emit(ProgressEvent("done", {"overall_total": total_pages, "overall_done": parsed_pages}))

    def _synthesize_alum(
        self,
        alum_name: str,
        entity_id: uuid.UUID,
        page_profiles: list[ExtractedProfile],
    ) -> SynthesizedProfile:
        class_year = self.store.get_class_year_for_entity(
            entity_id
        ) or self.store.get_class_year_for_alum(alum_name)
        positions = self.store.get_positions_for_alum(
            alum_name,
            set(SYNTHESIS_VERDICTS),
            entity_id=entity_id,
        )
        evidence = {
            "page_profiles": [profile.model_dump() for profile in page_profiles],
            "positions": positions,
            "facts": [
                {
                    "category": fact.category,
                    "content": fact.content,
                    "confidence": fact.confidence,
                    "validation_verdict": fact.validation_verdict,
                    "source_url": fact.raw_page.source_url if fact.raw_page else "",
                }
                for fact in self.store.list_facts_for_alum(
                    alum_name,
                    SYNTHESIS_VERDICTS,
                    entity_id=entity_id,
                )
            ],
            "connections": [
                {
                    "connected_name": connection.connected_name,
                    "context": connection.context,
                    "relationship_type": connection.relationship_type,
                    "validation_verdict": connection.validation_verdict,
                    "source_url": connection.raw_page.source_url if connection.raw_page else "",
                }
                for connection in self.store.list_connections_for_alum(
                    alum_name,
                    SYNTHESIS_VERDICTS,
                    entity_id=entity_id,
                )
            ],
            "projects": [
                {
                    "project_name": project.project_name,
                    "description": project.description,
                    "validation_verdict": project.validation_verdict,
                    "source_url": project.raw_page.source_url if project.raw_page else "",
                }
                for project in self.store.list_projects_for_alum(
                    alum_name,
                    SYNTHESIS_VERDICTS,
                    entity_id=entity_id,
                )
            ],
        }
        profile = self.synthesizer.synthesize(alum_name, class_year, evidence)
        first_current = next(
            (position for position in positions if position.get("is_current")),
            None,
        )
        self.store.upsert_profile(
            name=alum_name,
            entity_id=entity_id,
            class_year=class_year,
            current_company=str((first_current or {}).get("company", "")).strip()
            or profile.current_company,
            current_title=str((first_current or {}).get("title", "")).strip()
            or profile.current_title,
            past_companies=profile.past_companies,
            education=profile.education,
            bio_summary=profile.bio_summary,
            last_parsed_at=datetime.now(UTC),
        )
        return profile

    def _entity_id_for_raw_page(self, raw_page: RawPage) -> uuid.UUID:
        if raw_page.entity_id is not None:
            return raw_page.entity_id
        class_year = self.store.get_class_year_for_alum(raw_page.alum_name)
        context = {"source": "extracted_from_page"}
        if class_year:
            context["class_year"] = class_year
        with self.store.session() as session:
            entity_id = resolve_or_create(raw_page.alum_name, session=session, context=context)
            session.commit()
        self.store.set_raw_page_entity(raw_page.id, entity_id)
        raw_page.entity_id = entity_id
        return entity_id


def apply_validation(extraction: PageExtraction, validation: ValidationResult) -> None:
    _apply_item_verdicts(extraction.connections, validation.connection_verdicts)
    _apply_item_verdicts(extraction.projects, validation.project_verdicts)
    _apply_item_verdicts(extraction.facts, validation.fact_verdicts)
    _apply_item_verdicts(extraction.positions, validation.position_verdicts)


def extracted_profile_attributes(profile: ExtractedProfile) -> list[dict[str, object]]:
    attributes: list[dict[str, object]] = []
    for attribute_name in ("current_company", "current_title", "bio_summary"):
        value = getattr(profile, attribute_name).strip()
        if value:
            attributes.append(
                {
                    "attribute_name": attribute_name,
                    "attribute_value": value,
                    "confidence": "medium",
                    "validation_verdict": "keep",
                }
            )
    for company in profile.past_companies:
        cleaned = company.strip()
        if cleaned:
            attributes.append(
                {
                    "attribute_name": "past_company",
                    "attribute_value": cleaned,
                    "confidence": "medium",
                    "validation_verdict": "keep",
                }
            )
    for education in profile.education:
        cleaned = education.strip()
        if cleaned:
            attributes.append(
                {
                    "attribute_name": "education",
                    "attribute_value": cleaned,
                    "confidence": "medium",
                    "validation_verdict": "keep",
                }
            )
    return attributes


def _apply_item_verdicts(
    items: list[ExtractedConnection]
    | list[ExtractedProject]
    | list[ExtractedFact]
    | list[ExtractedPosition],
    verdicts: list[ItemVerdict],
) -> None:
    by_index = {verdict.index: verdict.verdict for verdict in verdicts}
    for index, item in enumerate(items):
        item.validation_verdict = by_index.get(index, item.validation_verdict)


def verdict_counts(extraction: PageExtraction) -> dict[str, int]:
    counts = {"keep": 0, "uncertain": 0, "drop": 0}
    for verdict in _iter_verdicts(extraction):
        counts[verdict] += 1
    return counts


def _iter_verdicts(extraction: PageExtraction) -> Iterable[ValidationVerdict]:
    for item in [
        *extraction.connections,
        *extraction.projects,
        *extraction.facts,
        *extraction.positions,
    ]:
        yield item.validation_verdict


def dedupe_strings(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def first_nonempty(values: Iterable[object]) -> str:
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    return ""


def iter_profile_list_values(profiles: Iterable[dict[str, object]], key: str) -> Iterable[object]:
    for profile in profiles:
        values = profile.get(key, [])
        if isinstance(values, list):
            yield from values
