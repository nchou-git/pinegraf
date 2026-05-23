from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, TypeVar
from urllib.parse import urlparse

import httpx
import openai
import tiktoken
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field, model_validator

from backend.db.models import RawPage
from backend.db.store import SYNTHESIS_VERDICTS, Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.openai_retry import async_retry_openai_call, retry_openai_call
from backend.pipeline.relationship_types import normalize_relationship_type
from backend.pricing import estimate_llm_dollars
from backend.resolution.embeddings import EmbeddingClient
from backend.resolution.entity_resolver import resolve_or_create

MAX_EXTRACTION_CHARS = 30_000
CHUNK_PROMPT = (
    "Extract source-grounded knowledge graph claims from this page chunk. "
    "Use only the chunk text. Return JSON arrays for people, organizations, "
    "claims, and projects. Each claim must be a complete tuple with an explicit "
    "subject_name, subject_type, predicate, object_type, and either object_name "
    "or object_value. Never use the page's primary alumnus as an implied subject; "
    "if the text does not say who the subject is, do not emit the claim. Every "
    "claim, project, person, and "
    "organization must include text_evidence copied verbatim from the chunk, max "
    "200 characters, and confidence from 0.0 to 1.0. Prefer precise sentence-level "
    "evidence over inference."
)
TRIAGE_PROMPT = (
    "Does this chunk mention any specific named person? Return only JSON with "
    "has_person boolean and confidence number from 0.0 to 1.0."
)
ValidationVerdict = Literal["keep", "uncertain", "drop"]
ExtractionTierMode = Literal["mini_only", "cascade", "frontier_only"]
ClaimObjectType = Literal[
    "person",
    "organization",
    "project",
    "role",
    "education",
    "location",
    "date",
    "text",
]
EntityKind = Literal["person", "organization"]
T = TypeVar("T")


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    char_start: int
    char_end: int
    text: str

    @property
    def sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()


ChunkEventEmitter = Callable[[str, RawPage, Chunk | None, dict[str, object], bool], None]


@dataclass(frozen=True)
class TriageOutcome:
    result: TriageResult
    cache_hit: bool = False


@dataclass(frozen=True)
class ModelExtractionOutcome:
    extraction: ChunkExtraction
    cache_hit: bool = False


@dataclass(frozen=True)
class ChunkExtractionOutcome:
    extraction: ChunkExtraction
    cache_hit: bool = False


class ExtractedProfile(BaseModel):
    current_company: str = ""
    current_title: str = ""
    past_companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    bio_summary: str = ""


class ExtractedConnection(BaseModel):
    subject_name: str = ""
    subject_context: str = ""
    subject_entity_id: uuid.UUID | None = None
    connected_name: str
    connected_context: str = ""
    connected_entity_id: uuid.UUID | None = None
    context: str = ""
    relationship_type: str = "associate"
    confidence_score: float | None = None
    text_evidence: str = ""
    derivation: str = ""
    validation_verdict: ValidationVerdict = "keep"


class ExtractedProject(BaseModel):
    subject_name: str = ""
    subject_context: str = ""
    subject_entity_id: uuid.UUID | None = None
    project_name: str
    description: str = ""
    confidence_score: float | None = None
    text_evidence: str = ""
    validation_verdict: ValidationVerdict = "keep"


class ExtractedFact(BaseModel):
    category: str = "general"
    content: str
    confidence: str = "low"
    confidence_score: float | None = None
    text_evidence: str = ""
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
    claims: list[ExtractedClaim] = Field(default_factory=list)
    connections: list[ExtractedConnection] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)
    positions: list[ExtractedPosition] = Field(default_factory=list)


class TriageResult(BaseModel):
    has_person: bool = False
    confidence: float = 0.0


class ExtractedPerson(BaseModel):
    name: str
    description: str = ""
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedOrganization(BaseModel):
    name: str
    description: str = ""
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedRelationship(BaseModel):
    source_name: str
    source_context: str = ""
    target_name: str
    target_context: str = ""
    relationship_type: str = "associate"
    context: str = ""
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractedClaim(BaseModel):
    subject_name: str
    subject_type: EntityKind = "person"
    subject_context: str = ""
    predicate: str
    object_name: str = ""
    object_context: str = ""
    object_value: str = ""
    object_type: ClaimObjectType = "text"
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_chunk_index: int | None = None
    prompt_version: str = ""
    subject_entity_id: uuid.UUID | None = None
    object_entity_id: uuid.UUID | None = None
    validation_verdict: ValidationVerdict = "keep"


class ExtractedGraphProject(BaseModel):
    project_name: str
    description: str = ""
    people: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ChunkExtraction(BaseModel):
    people: list[ExtractedPerson] = Field(default_factory=list)
    organizations: list[ExtractedOrganization] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    projects: list[ExtractedGraphProject] = Field(default_factory=list)


class ItemVerdict(BaseModel):
    index: int
    verdict: ValidationVerdict
    reason: str = ""


class ValidationResult(BaseModel):
    claim_verdicts: list[ItemVerdict] = Field(default_factory=list)
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
    async def extract_page_async(
        self,
        raw_page: RawPage,
        chunks: list[Chunk],
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> PageExtraction:
        extraction = await asyncio.to_thread(self.extract_page, raw_page, chunks)
        if emit_chunk_event is not None:
            for chunk in chunks:
                emit_chunk_event("chunk_done", raw_page, chunk, {}, True)
        return extraction

    def extract_page(self, raw_page: RawPage, chunks: list[Chunk]) -> PageExtraction:
        del chunks
        return self.extract(raw_page)

    def extract(self, raw_page: RawPage) -> PageExtraction:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class ValidationClient:
    async def validate_async(
        self,
        raw_page: RawPage,
        extraction: PageExtraction,
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> ValidationResult:
        del emit_chunk_event
        return await asyncio.to_thread(self.validate, raw_page, extraction)

    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class SynthesisClient:
    async def synthesize_async(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        return await asyncio.to_thread(self.synthesize, alum_name, class_year, evidence)

    def synthesize(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class OpenAIExtractionClient(ExtractionClient):
    def __init__(
        self,
        api_key: str,
        *,
        store: Store,
        mini_model: str = "gpt-5.4-mini",
        frontier_model: str = "gpt-5.4",
    ) -> None:
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.http_client = httpx.AsyncClient(http2=True, timeout=60.0)
        self.async_client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            http_client=self.http_client,
        )
        self.store = store
        self.mini_model = mini_model
        self.frontier_model = frontier_model
        self.tier_mode = _extraction_tier_mode()

    async def aclose(self) -> None:
        await self.http_client.aclose()

    def extract(self, raw_page: RawPage) -> PageExtraction:
        return self.extract_page(raw_page, chunk_page(raw_page.page_text))

    def extract_page(self, raw_page: RawPage, chunks: list[Chunk]) -> PageExtraction:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.extract_page_async(raw_page, chunks))

        if not chunks:
            return PageExtraction()

        triage_results = [self._triage_chunk(raw_page, chunk) for chunk in chunks]
        any_person = any(result.has_person for result in triage_results)
        full_indices = {
            index
            for index, result in enumerate(triage_results)
            if any_person or result.has_person or result.confidence <= 0.8
        }
        if not full_indices:
            return PageExtraction()

        chunk_extractions = [
            self._extract_chunk(raw_page, chunks[index]) for index in sorted(full_indices)
        ]
        return page_extraction_from_chunks(raw_page, chunk_extractions)

    async def extract_page_async(
        self,
        raw_page: RawPage,
        chunks: list[Chunk],
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> PageExtraction:
        if not chunks:
            return PageExtraction()

        triage_outcomes = await _run_chunk_queue(
            chunks,
            lambda _index, chunk: self._triage_chunk_async(
                raw_page,
                chunk,
                emit_chunk_event=emit_chunk_event,
            ),
        )
        triage_results = [outcome.result for outcome in triage_outcomes]
        any_person = any(result.has_person for result in triage_results)
        full_indices = {
            index
            for index, result in enumerate(triage_results)
            if any_person or result.has_person or result.confidence <= 0.8
        }
        skipped_indices = set(range(len(chunks))) - full_indices
        for index in sorted(skipped_indices):
            result = triage_results[index]
            if emit_chunk_event is not None:
                emit_chunk_event(
                    "chunk_skipped_triage",
                    raw_page,
                    chunks[index],
                    {"has_person": result.has_person, "confidence": result.confidence},
                    True,
                )
        if not full_indices:
            return PageExtraction()

        extraction_outcomes = await _run_chunk_queue(
            [chunks[index] for index in sorted(full_indices)],
            lambda _index, chunk: self._extract_chunk_async(
                raw_page,
                chunk,
                emit_chunk_event=emit_chunk_event,
            ),
        )
        return page_extraction_from_chunks(
            raw_page,
            [outcome.extraction for outcome in extraction_outcomes],
        )

    def _triage_chunk(self, raw_page: RawPage, chunk: Chunk) -> TriageResult:
        model = self.mini_model
        prompt_version = _prompt_version(TRIAGE_PROMPT, TriageResult)
        cached = self.store.get_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
        )
        if cached is not None:
            return TriageResult.model_validate(cached)

        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=model,
                input=[
                    {
                        "role": "system",
                        "content": TRIAGE_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Page URL: {raw_page.source_url}\n"
                            f"Chunk index: {chunk.chunk_index}\n\n"
                            f"Chunk text:\n{chunk.text}"
                        ),
                    },
                ],
                text_format=TriageResult,
            )
        )
        parsed = response.output_parsed or TriageResult()
        self._record_usage(response, model=model, purpose="extract_triage", raw_page=raw_page)
        self.store.set_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
            response_json=parsed.model_dump(),
        )
        return parsed

    async def _triage_chunk_async(
        self,
        raw_page: RawPage,
        chunk: Chunk,
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> TriageOutcome:
        model = self.mini_model
        prompt_version = _prompt_version(TRIAGE_PROMPT, TriageResult)
        cached = self.store.get_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
        )
        if cached is not None:
            return TriageOutcome(result=TriageResult.model_validate(cached), cache_hit=True)

        response = await _parse_openai_response_async(
            self.async_client,
            model=model,
            input_messages=[
                {
                    "role": "system",
                    "content": TRIAGE_PROMPT,
                },
                {
                    "role": "user",
                    "content": (
                        f"Page URL: {raw_page.source_url}\n"
                        f"Chunk index: {chunk.chunk_index}\n\n"
                        f"Chunk text:\n{chunk.text}"
                    ),
                },
            ],
            text_format=TriageResult,
            raw_page=raw_page,
            chunk=chunk,
            emit_chunk_event=emit_chunk_event,
        )
        parsed = response.output_parsed or TriageResult()
        self._record_usage(response, model=model, purpose="extract_triage", raw_page=raw_page)
        self.store.set_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
            response_json=parsed.model_dump(),
        )
        return TriageOutcome(result=parsed, cache_hit=False)

    def _extract_chunk(self, raw_page: RawPage, chunk: Chunk) -> ChunkExtraction:
        if self.tier_mode == "frontier_only":
            return self._extract_chunk_with_model(raw_page, chunk, self.frontier_model)

        mini_result = self._extract_chunk_with_model(raw_page, chunk, self.mini_model)
        if self.tier_mode == "mini_only" or not _has_low_confidence(mini_result):
            return mini_result

        frontier_result = self._extract_chunk_with_model(raw_page, chunk, self.frontier_model)
        return merge_chunk_extractions(mini_result, frontier_result)

    async def _extract_chunk_async(
        self,
        raw_page: RawPage,
        chunk: Chunk,
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> ChunkExtractionOutcome:
        if self.tier_mode == "frontier_only":
            outcome = await self._extract_chunk_with_model_async(
                raw_page,
                chunk,
                self.frontier_model,
                emit_chunk_event=emit_chunk_event,
            )
            _emit_full_chunk_terminal(emit_chunk_event, raw_page, chunk, outcome.cache_hit)
            return ChunkExtractionOutcome(
                extraction=outcome.extraction,
                cache_hit=outcome.cache_hit,
            )

        mini_outcome = await self._extract_chunk_with_model_async(
            raw_page,
            chunk,
            self.mini_model,
            emit_chunk_event=emit_chunk_event,
        )
        if self.tier_mode == "mini_only" or not _has_low_confidence(mini_outcome.extraction):
            _emit_full_chunk_terminal(emit_chunk_event, raw_page, chunk, mini_outcome.cache_hit)
            return ChunkExtractionOutcome(
                extraction=mini_outcome.extraction,
                cache_hit=mini_outcome.cache_hit,
            )

        if emit_chunk_event is not None:
            emit_chunk_event(
                "chunk_escalated", raw_page, chunk, {"model": self.frontier_model}, False
            )
        frontier_outcome = await self._extract_chunk_with_model_async(
            raw_page,
            chunk,
            self.frontier_model,
            emit_chunk_event=emit_chunk_event,
        )
        _emit_full_chunk_terminal(
            emit_chunk_event,
            raw_page,
            chunk,
            mini_outcome.cache_hit and frontier_outcome.cache_hit,
        )
        return ChunkExtractionOutcome(
            extraction=merge_chunk_extractions(
                mini_outcome.extraction, frontier_outcome.extraction
            ),
            cache_hit=mini_outcome.cache_hit and frontier_outcome.cache_hit,
        )

    def _extract_chunk_with_model(
        self,
        raw_page: RawPage,
        chunk: Chunk,
        model: str,
    ) -> ChunkExtraction:
        prompt_version = _prompt_version(CHUNK_PROMPT, ChunkExtraction)
        cached = self.store.get_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
        )
        if cached is not None:
            return ChunkExtraction.model_validate(cached)

        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": CHUNK_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Primary target alumnus, if known: {raw_page.alum_name or 'unknown'}\n"
                            f"Page URL: {raw_page.source_url}\n"
                            f"Page title: {raw_page.page_title}\n"
                            f"Chunk index: {chunk.chunk_index}\n"
                            f"Chunk char span: {chunk.char_start}-{chunk.char_end}\n\n"
                            f"Chunk text:\n{chunk.text}"
                        ),
                    },
                ],
                text_format=ChunkExtraction,
            )
        )
        parsed = response.output_parsed or ChunkExtraction()
        _stamp_claim_chunks(parsed, chunk, prompt_version)
        self._record_usage(response, model=model, purpose="extract_full", raw_page=raw_page)
        self.store.set_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
            response_json=parsed.model_dump(),
        )
        return parsed

    async def _extract_chunk_with_model_async(
        self,
        raw_page: RawPage,
        chunk: Chunk,
        model: str,
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> ModelExtractionOutcome:
        prompt_version = _prompt_version(CHUNK_PROMPT, ChunkExtraction)
        cached = self.store.get_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
        )
        if cached is not None:
            return ModelExtractionOutcome(
                extraction=ChunkExtraction.model_validate(cached),
                cache_hit=True,
            )

        response = await _parse_openai_response_async(
            self.async_client,
            model=model,
            input_messages=[
                {"role": "system", "content": CHUNK_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Primary target alumnus, if known: {raw_page.alum_name or 'unknown'}\n"
                        f"Page URL: {raw_page.source_url}\n"
                        f"Page title: {raw_page.page_title}\n"
                        f"Chunk index: {chunk.chunk_index}\n"
                        f"Chunk char span: {chunk.char_start}-{chunk.char_end}\n\n"
                        f"Chunk text:\n{chunk.text}"
                    ),
                },
            ],
            text_format=ChunkExtraction,
            raw_page=raw_page,
            chunk=chunk,
            emit_chunk_event=emit_chunk_event,
        )
        parsed = response.output_parsed or ChunkExtraction()
        _stamp_claim_chunks(parsed, chunk, prompt_version)
        self._record_usage(response, model=model, purpose="extract_full", raw_page=raw_page)
        self.store.set_extraction_cache(
            chunk_sha256=chunk.sha256,
            prompt_version=prompt_version,
            model=model,
            response_json=parsed.model_dump(),
        )
        return ModelExtractionOutcome(extraction=parsed, cache_hit=False)

    def _record_usage(
        self,
        response: object,
        *,
        model: str,
        purpose: str,
        raw_page: RawPage,
    ) -> None:
        prompt_tokens, completion_tokens = _usage_tokens(response)
        self.store.record_llm_usage(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            dollars=estimate_llm_dollars(
                model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            ),
            purpose=purpose,
            raw_page_id=raw_page.id,
            entity_id=raw_page.entity_id,
        )


class OpenAIValidationClient(ValidationClient):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4-mini",
        store: Store | None = None,
    ) -> None:
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.http_client = httpx.AsyncClient(http2=True, timeout=60.0)
        self.async_client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            http_client=self.http_client,
        )
        self.model = model
        self.store = store

    async def aclose(self) -> None:
        await self.http_client.aclose()

    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        payload = {
            "claims": [
                claim.model_dump(exclude={"validation_verdict"}) for claim in extraction.claims
            ],
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
            not payload["claims"]
            and not payload["connections"]
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
        if self.store is not None:
            prompt_tokens, completion_tokens = _usage_tokens(response)
            self.store.record_llm_usage(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                dollars=estimate_llm_dollars(
                    self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                purpose="extract_validation",
                raw_page_id=raw_page.id,
                entity_id=raw_page.entity_id,
            )
        return response.output_parsed or ValidationResult()

    async def validate_async(
        self,
        raw_page: RawPage,
        extraction: PageExtraction,
        *,
        emit_chunk_event: ChunkEventEmitter | None = None,
    ) -> ValidationResult:
        payload = {
            "claims": [
                claim.model_dump(exclude={"validation_verdict"}) for claim in extraction.claims
            ],
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
            not payload["claims"]
            and not payload["connections"]
            and not payload["projects"]
            and not payload["facts"]
            and not payload["positions"]
        ):
            return ValidationResult()

        response = await _parse_openai_response_async(
            self.async_client,
            model=self.model,
            input_messages=[
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
            raw_page=raw_page,
            chunk=None,
            emit_chunk_event=emit_chunk_event,
        )
        if self.store is not None:
            prompt_tokens, completion_tokens = _usage_tokens(response)
            self.store.record_llm_usage(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                dollars=estimate_llm_dollars(
                    self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                purpose="extract_validation",
                raw_page_id=raw_page.id,
                entity_id=raw_page.entity_id,
            )
        return response.output_parsed or ValidationResult()


class OpenAISynthesisClient(SynthesisClient):
    def __init__(self, api_key: str, model: str = "gpt-5.4", store: Store | None = None) -> None:
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.http_client = httpx.AsyncClient(http2=True, timeout=60.0)
        self.async_client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            http_client=self.http_client,
        )
        self.model = model
        self.store = store

    async def aclose(self) -> None:
        await self.http_client.aclose()

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
        if self.store is not None:
            prompt_tokens, completion_tokens = _usage_tokens(response)
            self.store.record_llm_usage(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                dollars=estimate_llm_dollars(
                    self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                purpose="profile_synthesis",
            )
        return response.output_parsed or SynthesizedProfile()

    async def synthesize_async(
        self,
        alum_name: str,
        class_year: str,
        evidence: dict[str, object],
    ) -> SynthesizedProfile:
        response = await _parse_openai_response_async(
            self.async_client,
            model=self.model,
            input_messages=[
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
            raw_page=None,
            chunk=None,
            emit_chunk_event=None,
        )
        if self.store is not None:
            prompt_tokens, completion_tokens = _usage_tokens(response)
            self.store.record_llm_usage(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                dollars=estimate_llm_dollars(
                    self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                ),
                purpose="profile_synthesis",
            )
        return response.output_parsed or SynthesizedProfile()


class MockExtractionClient(ExtractionClient):
    def extract(self, raw_page: RawPage) -> PageExtraction:
        lower = raw_page.page_text.lower()
        subject_name = _raw_page_subject_name(raw_page)
        first_name = subject_name.split()[0].lower() if subject_name.split() else ""
        connections: list[ExtractedConnection] = []
        projects: list[ExtractedProject] = []

        if "gyrobike" in lower and first_name in {"errik", "daniella"}:
            connected_name = "Daniella Reichstetter" if first_name == "errik" else "Errik Anderson"
            connections.append(
                ExtractedConnection(
                    subject_name=subject_name,
                    connected_name=connected_name,
                    context="Worked together on the Gyrobike first-year project at Tuck.",
                    relationship_type="project collaborator",
                    confidence_score=0.9,
                    text_evidence=(
                        "Errik Anderson and Daniella Reichstetter worked together on the "
                        "Gyrobike first-year project at Tuck."
                    ),
                )
            )
            projects.append(
                ExtractedProject(
                    subject_name=subject_name,
                    project_name="Gyrobike FYP",
                    description="Tuck first-year project involving gyrobike work.",
                    confidence_score=0.9,
                    text_evidence="Gyrobike first-year project at Tuck.",
                )
            )

        return PageExtraction(
            profile=ExtractedProfile(
                current_company="Acme Corp" if "acme" in lower else "",
                current_title="Senior Manager" if "senior manager" in lower else "",
                past_companies=["Beta Inc", "Gamma LLC"] if "beta" in lower else [],
                education=["Dartmouth Tuck MBA"] if "tuck" in lower else [],
                bio_summary=(
                    f"{subject_name} has stored public-page evidence from Pinegraf's mock parser."
                ),
            ),
            connections=connections,
            projects=projects,
            facts=[
                ExtractedFact(
                    category="career",
                    content=f"{subject_name} is described in a public page.",
                    confidence="medium",
                    confidence_score=0.7,
                    text_evidence=f"{subject_name} has stored public-page evidence.",
                )
            ],
            positions=[],
        )


class MockValidationClient(ValidationClient):
    def validate(self, raw_page: RawPage, extraction: PageExtraction) -> ValidationResult:
        del raw_page
        return ValidationResult(
            claim_verdicts=[
                ItemVerdict(index=index, verdict=claim.validation_verdict)
                for index, claim in enumerate(extraction.claims)
            ],
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
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.store = store
        self.extractor = extractor
        self.validator = validator
        self.synthesizer = synthesizer
        self.embedding_client = embedding_client

    def run(
        self,
        emit: Callable[[ProgressEvent], None],
        *,
        force: bool = False,
        url_pattern: str | None = None,
        keywords: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> None:
        asyncio.run(
            self.run_async(
                emit,
                force=force,
                url_pattern=url_pattern,
                keywords=keywords,
                limit=limit,
            )
        )

    async def run_async(
        self,
        emit: Callable[[ProgressEvent], None],
        *,
        force: bool = False,
        url_pattern: str | None = None,
        keywords: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> None:
        keyword_list = list(keywords or [])
        pages = self.store.list_pages_to_parse(
            force=force,
            url_pattern=url_pattern,
            keywords=keyword_list,
            limit=limit,
        )
        total_pages = len(pages)
        parsed_pages = 0
        run_started_at = datetime.now(UTC)

        pages_by_alum: dict[tuple[str, uuid.UUID], list[RawPage]] = defaultdict(list)
        page_entity_ids: dict[int, uuid.UUID] = {}
        page_subject_names: dict[int, str] = {}
        for page in pages:
            subject_name = _raw_page_subject_name(page)
            entity_id = self._entity_id_for_raw_page(page)
            page_subject_names[page.id] = subject_name
            page_entity_ids[page.id] = entity_id
            pages_by_alum[(subject_name, entity_id)].append(page)

        chunks_by_page_id = {page.id: chunk_page(page.page_text) for page in pages}
        total_chunks = sum(len(chunks) for chunks in chunks_by_page_id.values())
        completed_chunks = 0
        page_metadata: dict[int, tuple[int, int]] = {}
        for _group_key, alum_pages in pages_by_alum.items():
            for page_index, page in enumerate(alum_pages, start=1):
                page_metadata[page.id] = (page_index, len(alum_pages))

        emit(
            ProgressEvent(
                "parse_start",
                {
                    "page_total": total_pages,
                    "page_done": parsed_pages,
                    "chunk_total": total_chunks,
                    "force": force,
                    "url_pattern": url_pattern,
                    "keywords": keyword_list,
                    "limit": limit,
                    "overall_total": total_chunks,
                    "overall_done": completed_chunks,
                },
            )
        )

        def emit_chunk_event(
            kind: str,
            raw_page: RawPage,
            chunk: Chunk | None,
            data: dict[str, object],
            terminal: bool,
        ) -> None:
            nonlocal completed_chunks
            if terminal:
                completed_chunks += 1
            page_index, page_total = page_metadata.get(raw_page.id, (0, 0))
            payload: dict[str, object] = {
                "raw_page_id": raw_page.id,
                "name": page_subject_names.get(raw_page.id, raw_page.alum_name),
                "url": raw_page.source_url,
                "page_index": page_index,
                "page_total": page_total,
                "page_done": parsed_pages,
                "overall_total": total_chunks,
                "overall_done": completed_chunks,
            }
            if chunk is not None:
                payload.update(
                    {
                        "chunk_index": chunk.chunk_index,
                        "chunk_char_start": chunk.char_start,
                        "chunk_char_end": chunk.char_end,
                    }
                )
            payload.update(data)
            emit(ProgressEvent(kind, payload))

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

        usage_ticker_done = asyncio.Event()

        async def emit_usage_ticks() -> None:
            while not usage_ticker_done.is_set():
                try:
                    await asyncio.wait_for(usage_ticker_done.wait(), timeout=5.0)
                except TimeoutError:
                    emit(
                        ProgressEvent(
                            "usage_tick",
                            self.store.llm_usage_totals_since(run_started_at),
                        )
                    )
            emit(
                ProgressEvent(
                    "usage_tick",
                    self.store.llm_usage_totals_since(run_started_at),
                )
            )

        usage_ticker = asyncio.create_task(emit_usage_ticks())
        page_semaphore = asyncio.Semaphore(_parse_concurrency())

        async def parse_page(
            raw_page: RawPage,
        ) -> tuple[tuple[str, uuid.UUID], int, ExtractedProfile]:
            nonlocal parsed_pages
            async with page_semaphore:
                entity_id = page_entity_ids[raw_page.id]
                subject_name = page_subject_names[raw_page.id]
                group_key = (subject_name, entity_id)
                page_index, page_total = page_metadata[raw_page.id]
                chunks = chunks_by_page_id[raw_page.id]
                embeddings = await asyncio.to_thread(
                    self._chunk_embeddings,
                    raw_page,
                    chunks,
                )
                self.store.replace_page_chunks(
                    raw_page_id=raw_page.id,
                    chunks=chunks,
                    embeddings=embeddings,
                )
                extraction = await self.extractor.extract_page_async(
                    raw_page,
                    chunks,
                    emit_chunk_event=emit_chunk_event,
                )
                validation = await self.validator.validate_async(
                    raw_page,
                    extraction,
                    emit_chunk_event=emit_chunk_event,
                )
                apply_validation(extraction, validation)
                self._resolve_extraction_entities(raw_page, extraction)
                self.store.replace_structured_items(
                    raw_page_id=raw_page.id,
                    alum_name=subject_name,
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
                                "confidence_score": _confidence_score_from_label(
                                    position.confidence
                                ),
                                "validation_verdict": position.validation_verdict,
                            }
                            for position in extraction.positions
                        ],
                    ],
                    claims=[claim_to_store_dict(claim) for claim in extraction.claims],
                    connections=[connection.model_dump() for connection in extraction.connections],
                    projects=[project.model_dump() for project in extraction.projects],
                )
                self.store.replace_entity_attributes(
                    entity_id=entity_id,
                    source_url=raw_page.source_url,
                    attributes=extracted_profile_attributes(extraction.profile),
                )
                self.store.mark_raw_page_parsed(raw_page.id)
                parsed_pages += 1
                emit(
                    ProgressEvent(
                        "page_parsed",
                        {
                            "name": subject_name,
                            "raw_page_id": raw_page.id,
                            "url": raw_page.source_url,
                            "page_index": page_index,
                            "page_total": page_total,
                            "page_done": parsed_pages,
                            "overall_total": total_chunks,
                            "overall_done": completed_chunks,
                            "chunk_total": len(chunks),
                            "verdict_counts": verdict_counts(extraction),
                        },
                    )
                )
                return group_key, page_index, extraction.profile

        try:
            page_results = await asyncio.gather(*(parse_page(page) for page in pages))
            page_profiles_by_alum: dict[
                tuple[str, uuid.UUID], list[tuple[int, ExtractedProfile]]
            ] = defaultdict(list)
            for group_key, page_index, page_profile in page_results:
                page_profiles_by_alum[group_key].append((page_index, page_profile))

            for alum_name, entity_id in pages_by_alum:
                page_profiles = [
                    profile
                    for _page_index, profile in sorted(
                        page_profiles_by_alum[(alum_name, entity_id)],
                        key=lambda item: item[0],
                    )
                ]
                profile = await self._synthesize_alum_async(alum_name, entity_id, page_profiles)
                emit(
                    ProgressEvent(
                        "alum_done",
                        {
                            "name": alum_name,
                            "page_total": len(pages_by_alum[(alum_name, entity_id)]),
                            "overall_total": total_chunks,
                            "overall_done": completed_chunks,
                            "page_done": parsed_pages,
                            "page_total_all": total_pages,
                            "current_company": profile.current_company,
                        },
                    )
                )

            emit(
                ProgressEvent(
                    "done",
                    {
                        "overall_total": total_chunks,
                        "overall_done": completed_chunks,
                        "page_total": total_pages,
                        "page_done": parsed_pages,
                    },
                )
            )
        finally:
            usage_ticker_done.set()
            await usage_ticker
            await self.extractor.aclose()
            await self.validator.aclose()
            await self.synthesizer.aclose()

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
                    "confidence_score": fact.confidence_score,
                    "text_evidence": fact.text_evidence,
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
                    "confidence_score": connection.confidence_score,
                    "text_evidence": connection.text_evidence,
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
                    "confidence_score": project.confidence_score,
                    "text_evidence": project.text_evidence,
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

    async def _synthesize_alum_async(
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
                    "confidence_score": fact.confidence_score,
                    "text_evidence": fact.text_evidence,
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
                    "confidence_score": connection.confidence_score,
                    "text_evidence": connection.text_evidence,
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
                    "confidence_score": project.confidence_score,
                    "text_evidence": project.text_evidence,
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
        profile = await self.synthesizer.synthesize_async(alum_name, class_year, evidence)
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
        subject_name = _raw_page_subject_name(raw_page)
        class_year = self.store.get_class_year_for_alum(subject_name)
        context = {"source": "extracted_from_page"}
        if class_year:
            context["class_year"] = class_year
        with self.store.session() as session:
            entity_id = resolve_or_create(
                subject_name,
                session=session,
                context=context,
                embedding_client=self.embedding_client,
            )
            session.commit()
        self.store.set_raw_page_entity(raw_page.id, entity_id)
        raw_page.entity_id = entity_id
        return entity_id

    def _resolve_extraction_entities(
        self,
        raw_page: RawPage,
        extraction: PageExtraction,
    ) -> None:
        _ensure_claims_for_explicit_items(extraction)
        _ensure_projection_items_for_claims(extraction)
        with self.store.session() as session:
            for claim in extraction.claims:
                if claim.validation_verdict == "drop":
                    continue
                subject_name = claim.subject_name.strip()
                predicate = claim.predicate.strip()
                if not subject_name or not predicate:
                    claim.validation_verdict = "drop"
                    continue
                object_name = claim.object_name.strip()
                object_value = claim.object_value.strip()
                if not object_name and not object_value:
                    claim.validation_verdict = "drop"
                    continue
                subject_context = _claim_context(
                    raw_page, claim.subject_context, claim.text_evidence
                )
                if _names_match(subject_name, raw_page.alum_name or ""):
                    page_class_year = self.store.get_class_year_for_entity(
                        raw_page.entity_id
                    ) or self.store.get_class_year_for_alum(raw_page.alum_name)
                    if page_class_year:
                        subject_context["class_year"] = page_class_year
                claim.subject_entity_id = resolve_or_create(
                    subject_name,
                    session=session,
                    context=subject_context,
                    embedding_client=self.embedding_client,
                    entity_type=claim.subject_type,
                )
                if object_name and claim.object_type in {"person", "organization", "project"}:
                    object_context = _claim_context(
                        raw_page,
                        claim.object_context,
                        claim.text_evidence,
                    )
                    if _names_match(object_name, raw_page.alum_name or ""):
                        page_class_year = self.store.get_class_year_for_entity(
                            raw_page.entity_id
                        ) or self.store.get_class_year_for_alum(raw_page.alum_name)
                        if page_class_year:
                            object_context["class_year"] = page_class_year
                    claim.object_entity_id = resolve_or_create(
                        object_name,
                        session=session,
                        context=object_context,
                        embedding_client=self.embedding_client,
                        entity_type=_claim_object_entity_type(claim.object_type),
                    )
            session.commit()

        claims_by_projection_key = {
            (
                claim.subject_name.casefold(),
                (claim.object_name or claim.object_value).casefold(),
                claim.predicate.casefold(),
                claim.text_evidence.casefold(),
            ): claim
            for claim in extraction.claims
        }
        project_claims_by_key = {
            (
                claim.subject_name.casefold(),
                claim.object_name.casefold(),
                claim.text_evidence.casefold(),
            ): claim
            for claim in extraction.claims
            if claim.object_type == "project" and claim.object_name
        }
        for connection in extraction.connections:
            key = (
                connection.subject_name.casefold(),
                connection.connected_name.casefold(),
                connection.relationship_type.casefold(),
                connection.text_evidence.casefold(),
            )
            claim = claims_by_projection_key.get(key)
            if claim is None or claim.validation_verdict == "drop":
                connection.validation_verdict = "drop"
                continue
            connection.subject_entity_id = claim.subject_entity_id
            connection.connected_entity_id = claim.object_entity_id
            if connection.subject_entity_id is None or connection.connected_entity_id is None:
                connection.validation_verdict = "drop"

        for project in extraction.projects:
            key = (
                project.subject_name.casefold(),
                project.project_name.casefold(),
                project.text_evidence.casefold(),
            )
            claim = project_claims_by_key.get(key)
            if claim is None or claim.validation_verdict == "drop":
                project.validation_verdict = "drop"
                continue
            project.subject_entity_id = claim.subject_entity_id
            if project.subject_entity_id is None:
                project.validation_verdict = "drop"

    def _chunk_embeddings(self, raw_page: RawPage, chunks: list[Chunk]) -> list[list[float] | None]:
        if self.embedding_client is None:
            return []
        return [
            self.embedding_client.embed_text(
                chunk.text,
                purpose="page_chunk_embedding",
                entity_id=raw_page.entity_id,
            )
            for chunk in chunks
        ]


class OpenAIRateLimiter:
    def __init__(self) -> None:
        self._pause_until_monotonic = 0.0
        self._lock = asyncio.Lock()

    async def wait_if_paused(
        self,
        *,
        raw_page: RawPage | None,
        chunk: Chunk | None,
        emit_chunk_event: ChunkEventEmitter | None,
    ) -> None:
        while True:
            async with self._lock:
                delay = self._pause_until_monotonic - time.monotonic()
            if delay <= 0:
                return
            if emit_chunk_event is not None and raw_page is not None:
                emit_chunk_event(
                    "rate_limit_pause",
                    raw_page,
                    chunk,
                    {"pause_seconds": round(delay, 3)},
                    False,
                )
            await asyncio.sleep(delay)

    async def observe_headers(self, headers: object) -> None:
        request_limit = _header_float(headers, "x-ratelimit-limit-requests")
        request_remaining = _header_float(headers, "x-ratelimit-remaining-requests")
        token_limit = _header_float(headers, "x-ratelimit-limit-tokens")
        token_remaining = _header_float(headers, "x-ratelimit-remaining-tokens")
        request_low = (
            request_limit is not None
            and request_limit > 0
            and request_remaining is not None
            and request_remaining / request_limit < 0.1
        )
        token_low = (
            token_limit is not None
            and token_limit > 0
            and token_remaining is not None
            and token_remaining / token_limit < 0.1
        )
        if not request_low and not token_low:
            return
        pause_seconds = max(1.0, 60.0 - (time.time() % 60.0))
        async with self._lock:
            self._pause_until_monotonic = max(
                self._pause_until_monotonic,
                time.monotonic() + pause_seconds,
            )


_OPENAI_RATE_LIMITER = OpenAIRateLimiter()
_OPENAI_SEMAPHORES: dict[int, tuple[int, asyncio.Semaphore]] = {}


async def _run_chunk_queue(
    chunks: list[Chunk],
    worker_fn: Callable[[int, Chunk], object],
) -> list[object]:
    queue: asyncio.Queue[tuple[int, Chunk]] = asyncio.Queue()
    for index, chunk in enumerate(chunks):
        queue.put_nowait((index, chunk))

    results: dict[int, object] = {}

    async def worker() -> None:
        while True:
            try:
                index, chunk = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                result = worker_fn(index, chunk)
                if asyncio.iscoroutine(result):
                    result = await result
                results[index] = result
            finally:
                queue.task_done()

    worker_count = min(max(1, _parse_concurrency()), max(1, len(chunks)))
    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return [results[index] for index in sorted(results)]


async def _parse_openai_response_async(
    client: AsyncOpenAI,
    *,
    model: str,
    input_messages: list[dict[str, str]],
    text_format: type[BaseModel],
    raw_page: RawPage | None,
    chunk: Chunk | None,
    emit_chunk_event: ChunkEventEmitter | None,
    rate_limiter: OpenAIRateLimiter = _OPENAI_RATE_LIMITER,
) -> object:
    def on_backoff(exc: BaseException, delay: float) -> None:
        if not isinstance(exc, openai.RateLimitError):
            return
        if emit_chunk_event is None or raw_page is None:
            return
        emit_chunk_event(
            "rate_limit_pause",
            raw_page,
            chunk,
            {"pause_seconds": delay, "reason": "429"},
            False,
        )

    async def call() -> object:
        await rate_limiter.wait_if_paused(
            raw_page=raw_page,
            chunk=chunk,
            emit_chunk_event=emit_chunk_event,
        )
        async with _openai_dispatch_semaphore():
            raw_response = await client.responses.with_raw_response.parse(
                model=model,
                input=input_messages,
                text_format=text_format,
            )
        await rate_limiter.observe_headers(raw_response.headers)
        return raw_response.parse()

    return await async_retry_openai_call(call, on_backoff=on_backoff)


def _openai_dispatch_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    concurrency = _parse_concurrency()
    existing = _OPENAI_SEMAPHORES.get(loop_id)
    if existing is None or existing[0] != concurrency:
        semaphore = asyncio.Semaphore(concurrency)
        _OPENAI_SEMAPHORES[loop_id] = (concurrency, semaphore)
        return semaphore
    return existing[1]


def _emit_full_chunk_terminal(
    emit_chunk_event: ChunkEventEmitter | None,
    raw_page: RawPage,
    chunk: Chunk,
    cache_hit: bool,
) -> None:
    if emit_chunk_event is None:
        return
    if cache_hit:
        emit_chunk_event("chunk_skipped_cache", raw_page, chunk, {}, True)
    else:
        emit_chunk_event("chunk_done", raw_page, chunk, {}, True)


def _stamp_claim_chunks(
    extraction: ChunkExtraction,
    chunk: Chunk,
    prompt_version: str,
) -> None:
    for claim in extraction.claims:
        claim.source_chunk_index = chunk.chunk_index
        if not claim.prompt_version:
            claim.prompt_version = prompt_version


def chunk_page(
    page_text: str,
    *,
    target_tokens: int = 4000,
    overlap_tokens: int = 200,
) -> list[Chunk]:
    cleaned = page_text or ""
    if not cleaned:
        return []
    encoder = tiktoken.get_encoding("cl100k_base")
    tokens = encoder.encode(cleaned)
    if len(tokens) <= target_tokens:
        return [Chunk(chunk_index=0, char_start=0, char_end=len(cleaned), text=cleaned)]

    chunks: list[Chunk] = []
    step = max(1, target_tokens - overlap_tokens)
    search_from = 0
    for chunk_index, token_start in enumerate(range(0, len(tokens), step)):
        token_end = min(token_start + target_tokens, len(tokens))
        chunk_text = encoder.decode(tokens[token_start:token_end])
        char_start = cleaned.find(chunk_text, max(0, search_from - 4000))
        if char_start < 0:
            probe = chunk_text[: min(100, len(chunk_text))]
            char_start = cleaned.find(probe, max(0, search_from - 4000))
        if char_start < 0:
            char_start = search_from
        char_end = min(len(cleaned), char_start + len(chunk_text))
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                char_start=char_start,
                char_end=char_end,
                text=cleaned[char_start:char_end],
            )
        )
        if token_end >= len(tokens):
            break
        search_from = char_end
    return chunks


def page_extraction_from_chunks(
    raw_page: RawPage,
    chunk_extractions: Iterable[ChunkExtraction],
) -> PageExtraction:
    del raw_page
    extraction = PageExtraction()
    seen_claims: set[tuple[str, str, str, str]] = set()
    seen_connections: set[tuple[str, str, str, str]] = set()
    seen_projects: set[tuple[str, str]] = set()
    for chunk_extraction in chunk_extractions:
        for claim in chunk_extraction.claims:
            _append_claim_projection(
                extraction,
                claim,
                seen_claims=seen_claims,
                seen_connections=seen_connections,
                seen_projects=seen_projects,
            )
        for relationship in chunk_extraction.relationships:
            claim = ExtractedClaim(
                subject_name=relationship.source_name,
                subject_context=relationship.source_context,
                predicate=relationship.relationship_type or "associate",
                object_name=relationship.target_name,
                object_context=relationship.target_context,
                object_type="organization",
                text_evidence=relationship.text_evidence,
                confidence=relationship.confidence,
            )
            _append_claim_projection(
                extraction,
                claim,
                context=relationship.context,
                seen_claims=seen_claims,
                seen_connections=seen_connections,
                seen_projects=seen_projects,
            )
        for project in chunk_extraction.projects:
            project_name = project.project_name.strip()
            if not project_name:
                continue
            for person_name in project.people:
                claim = ExtractedClaim(
                    subject_name=person_name,
                    predicate="worked_on_project",
                    object_name=project_name,
                    object_type="project",
                    text_evidence=project.text_evidence,
                    confidence=project.confidence,
                )
                _append_claim_projection(
                    extraction,
                    claim,
                    context=project.description,
                    seen_claims=seen_claims,
                    seen_connections=seen_connections,
                    seen_projects=seen_projects,
                )
    return extraction


def _append_claim_projection(
    extraction: PageExtraction,
    claim: ExtractedClaim,
    *,
    seen_claims: set[tuple[str, str, str, str]],
    seen_connections: set[tuple[str, str, str, str]],
    seen_projects: set[tuple[str, str]],
    context: str = "",
) -> None:
    subject_name = claim.subject_name.strip()
    predicate = claim.predicate.strip()
    object_name = claim.object_name.strip()
    object_value = claim.object_value.strip()
    if not subject_name or not predicate or not (object_name or object_value):
        return
    normalized_type = normalize_relationship_type(predicate)
    predicate = normalized_type.relationship_type
    object_key = object_name or object_value
    claim_key = (
        subject_name.casefold(),
        predicate.casefold(),
        object_key.casefold(),
        claim.text_evidence.casefold(),
    )
    if claim_key in seen_claims:
        return
    seen_claims.add(claim_key)

    cleaned_claim = claim.model_copy(
        update={
            "subject_name": subject_name,
            "predicate": predicate,
            "object_name": object_name,
            "object_value": object_value,
        }
    )
    extraction.claims.append(cleaned_claim)

    if object_name and claim.object_type in {"person", "organization", "project"}:
        connection_key = (
            subject_name.casefold(),
            object_name.casefold(),
            predicate.casefold(),
            claim.text_evidence.casefold(),
        )
        if connection_key not in seen_connections:
            seen_connections.add(connection_key)
            extraction.connections.append(
                ExtractedConnection(
                    subject_name=subject_name,
                    subject_context=claim.subject_context,
                    connected_name=object_name,
                    connected_context=claim.object_context,
                    context=context or claim.text_evidence,
                    relationship_type=predicate,
                    confidence_score=claim.confidence,
                    text_evidence=claim.text_evidence,
                    derivation=normalized_type.derivation,
                )
            )

    if object_name and claim.object_type == "project":
        project_key = (subject_name.casefold(), object_name.casefold())
        if project_key in seen_projects:
            return
        seen_projects.add(project_key)
        extraction.projects.append(
            ExtractedProject(
                subject_name=subject_name,
                subject_context=claim.subject_context,
                project_name=object_name,
                description=context or claim.text_evidence,
                confidence_score=claim.confidence,
                text_evidence=claim.text_evidence,
            )
        )


def merge_chunk_extractions(
    primary: ChunkExtraction,
    secondary: ChunkExtraction,
) -> ChunkExtraction:
    merged = ChunkExtraction(
        people=[*primary.people],
        organizations=[*primary.organizations],
        claims=[*primary.claims],
        relationships=[*primary.relationships],
        projects=[*primary.projects],
    )
    _append_unique(merged.people, secondary.people, lambda item: item.name.casefold())
    _append_unique(merged.organizations, secondary.organizations, lambda item: item.name.casefold())
    _append_unique(
        merged.claims,
        secondary.claims,
        lambda item: (
            item.subject_name.casefold(),
            item.predicate.casefold(),
            (item.object_name or item.object_value).casefold(),
        ),
    )
    _append_unique(
        merged.relationships,
        secondary.relationships,
        lambda item: (
            item.source_name.casefold(),
            item.target_name.casefold(),
            item.relationship_type.casefold(),
        ),
    )
    _append_unique(merged.projects, secondary.projects, lambda item: item.project_name.casefold())
    return merged


def apply_validation(extraction: PageExtraction, validation: ValidationResult) -> None:
    _apply_item_verdicts(extraction.claims, validation.claim_verdicts)
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


def claim_to_store_dict(claim: ExtractedClaim) -> dict[str, object]:
    return {
        "subject_entity_id": str(claim.subject_entity_id) if claim.subject_entity_id else None,
        "subject_name": claim.subject_name,
        "predicate": claim.predicate,
        "object_entity_id": str(claim.object_entity_id) if claim.object_entity_id else None,
        "object_name": claim.object_name,
        "object_value": claim.object_value,
        "object_type": claim.object_type,
        "source_chunk_index": claim.source_chunk_index,
        "text_evidence": claim.text_evidence,
        "confidence_score": claim.confidence,
        "prompt_version": claim.prompt_version,
        "validation_verdict": claim.validation_verdict,
    }


def _ensure_claims_for_explicit_items(extraction: PageExtraction) -> None:
    seen = {
        (
            claim.subject_name.casefold(),
            claim.predicate.casefold(),
            (claim.object_name or claim.object_value).casefold(),
            claim.text_evidence.casefold(),
        )
        for claim in extraction.claims
    }
    for connection in extraction.connections:
        subject_name = connection.subject_name.strip()
        connected_name = connection.connected_name.strip()
        predicate = connection.relationship_type.strip() or "associate"
        if not subject_name or not connected_name:
            continue
        key = (
            subject_name.casefold(),
            predicate.casefold(),
            connected_name.casefold(),
            connection.text_evidence.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        extraction.claims.append(
            ExtractedClaim(
                subject_name=subject_name,
                subject_context=connection.subject_context,
                predicate=predicate,
                object_name=connected_name,
                object_context=connection.connected_context,
                object_type="organization",
                text_evidence=connection.text_evidence,
                confidence=connection.confidence_score or 0.5,
                validation_verdict=connection.validation_verdict,
            )
        )
    for project in extraction.projects:
        subject_name = project.subject_name.strip()
        project_name = project.project_name.strip()
        if not subject_name or not project_name:
            continue
        key = (
            subject_name.casefold(),
            "worked_on_project",
            project_name.casefold(),
            project.text_evidence.casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        extraction.claims.append(
            ExtractedClaim(
                subject_name=subject_name,
                subject_context=project.subject_context,
                predicate="worked_on_project",
                object_name=project_name,
                object_type="project",
                text_evidence=project.text_evidence,
                confidence=project.confidence_score or 0.5,
                validation_verdict=project.validation_verdict,
            )
        )


def _ensure_projection_items_for_claims(extraction: PageExtraction) -> None:
    seen_connections = {
        (
            connection.subject_name.casefold(),
            connection.connected_name.casefold(),
            connection.relationship_type.casefold(),
            connection.text_evidence.casefold(),
        )
        for connection in extraction.connections
    }
    seen_projects = {
        (
            project.subject_name.casefold(),
            project.project_name.casefold(),
            project.text_evidence.casefold(),
        )
        for project in extraction.projects
    }
    for claim in extraction.claims:
        subject_name = claim.subject_name.strip()
        object_name = claim.object_name.strip()
        predicate = claim.predicate.strip() or "associate"
        normalized_type = normalize_relationship_type(predicate)
        predicate = normalized_type.relationship_type
        if not subject_name or not object_name:
            continue
        if claim.object_type in {"person", "organization", "project"}:
            key = (
                subject_name.casefold(),
                object_name.casefold(),
                predicate.casefold(),
                claim.text_evidence.casefold(),
            )
            if key not in seen_connections:
                seen_connections.add(key)
                extraction.connections.append(
                    ExtractedConnection(
                        subject_name=subject_name,
                        subject_context=claim.subject_context,
                        connected_name=object_name,
                        connected_context=claim.object_context,
                        context=claim.text_evidence,
                        relationship_type=predicate,
                        confidence_score=claim.confidence,
                        text_evidence=claim.text_evidence,
                        derivation=normalized_type.derivation,
                        validation_verdict=claim.validation_verdict,
                    )
                )
        if claim.object_type != "project":
            continue
        project_key = (
            subject_name.casefold(),
            object_name.casefold(),
            claim.text_evidence.casefold(),
        )
        if project_key in seen_projects:
            continue
        seen_projects.add(project_key)
        extraction.projects.append(
            ExtractedProject(
                subject_name=subject_name,
                subject_context=claim.subject_context,
                project_name=object_name,
                description=claim.text_evidence,
                confidence_score=claim.confidence,
                text_evidence=claim.text_evidence,
                validation_verdict=claim.validation_verdict,
            )
        )


def _claim_context(
    raw_page: RawPage,
    model_context: str,
    text_evidence: str,
) -> dict[str, str]:
    context: dict[str, str] = {"source": f"raw_page:{raw_page.id}"}
    combined = " ".join([model_context, text_evidence, raw_page.page_title])
    class_year = _class_year_from_text(combined)
    if class_year:
        context["class_year"] = class_year
    if model_context.strip():
        context["evidence_context"] = model_context.strip()[:500]
    return context


def _class_year_from_text(value: str) -> str | None:
    match = re.search(r"T['’]\d{2}", value)
    if not match:
        return None
    return match.group(0).replace("’", "'")


def _claim_object_entity_type(object_type: str) -> str:
    return "person" if object_type == "person" else "organization"


def _apply_item_verdicts(
    items: list[ExtractedClaim]
    | list[ExtractedConnection]
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
        *extraction.claims,
        *extraction.connections,
        *extraction.projects,
        *extraction.facts,
        *extraction.positions,
    ]:
        yield item.validation_verdict


def _connected_name_for_relationship(
    raw_page: RawPage,
    relationship: ExtractedRelationship,
) -> str:
    source = relationship.source_name.strip()
    target = relationship.target_name.strip()
    alum = (raw_page.alum_name or "").strip()
    if alum and _names_match(source, alum):
        return target
    if alum and _names_match(target, alum):
        return source
    return target or source


def _project_mentions_alum(raw_page: RawPage, project: ExtractedGraphProject) -> bool:
    alum = (raw_page.alum_name or "").strip()
    if not alum:
        return False
    if any(_names_match(person, alum) for person in project.people):
        return True
    return _normalize_name(alum) in _normalize_name(project.text_evidence)


def _names_match(left: str, right: str) -> bool:
    return _normalize_name(left) == _normalize_name(right)


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _append_unique(target: list[T], incoming: Iterable[T], key_fn: Callable[[T], object]) -> None:
    seen = {key_fn(item) for item in target}
    for item in incoming:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        target.append(item)


def _has_low_confidence(extraction: ChunkExtraction) -> bool:
    values = [
        *[person.confidence for person in extraction.people],
        *[organization.confidence for organization in extraction.organizations],
        *[claim.confidence for claim in extraction.claims],
        *[relationship.confidence for relationship in extraction.relationships],
        *[project.confidence for project in extraction.projects],
    ]
    return any(value < 0.6 for value in values)


def _confidence_label(confidence: float | None) -> str:
    value = confidence if confidence is not None else 0.0
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def _confidence_score_from_label(confidence: str) -> float:
    return {"high": 0.9, "medium": 0.65, "low": 0.35}.get(confidence, 0.5)


def _prompt_version(prompt: str, model_type: type[BaseModel]) -> str:
    payload = json.dumps(model_type.model_json_schema(), sort_keys=True)
    return sha256(f"{prompt}\n{payload}".encode("utf-8")).hexdigest()


def _usage_tokens(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    prompt_tokens = (
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
    )
    completion_tokens = (
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
    )
    return int(prompt_tokens), int(completion_tokens)


def _extraction_tier_mode() -> ExtractionTierMode:
    value = os.getenv("EXTRACTION_TIER_MODE", "cascade").strip().lower()
    if value in {"mini_only", "cascade", "frontier_only"}:
        return value  # type: ignore[return-value]
    return "cascade"


def _parse_concurrency() -> int:
    try:
        return max(1, int(os.getenv("PARSE_CONCURRENCY", "8")))
    except ValueError:
        return 8


def _raw_page_subject_name(raw_page: RawPage) -> str:
    for value in (raw_page.alum_name, raw_page.page_title):
        cleaned = str(value or "").strip()
        if cleaned:
            return cleaned
    slug = urlparse(raw_page.source_url).path.rstrip("/").split("/")[-1]
    cleaned_slug = re.sub(r"[-_]+", " ", slug).strip()
    return cleaned_slug.title() if cleaned_slug else "Unknown"


def _header_float(headers: object, name: str) -> float | None:
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    value = getter(name)
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


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
