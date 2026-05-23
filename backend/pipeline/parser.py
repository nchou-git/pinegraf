from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, TypeVar

import httpx
import openai
import tiktoken
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, Field, model_validator

from backend.db.models import RawPage
from backend.db.store import SYNTHESIS_VERDICTS, Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.openai_retry import async_retry_openai_call, retry_openai_call
from backend.pricing import estimate_llm_dollars
from backend.resolution.embeddings import EmbeddingClient
from backend.resolution.entity_resolver import resolve_or_create

MAX_EXTRACTION_CHARS = 30_000
CHUNK_PROMPT = (
    "Extract a source-grounded people knowledge graph from this page chunk. "
    "Use only the chunk text. Return JSON arrays for people, organizations, "
    "relationships, and projects. Every item must include text_evidence copied "
    "verbatim from the chunk, max 200 characters, and confidence from 0.0 to 1.0. "
    "Prefer precise direct evidence over inference."
)
TRIAGE_PROMPT = (
    "Does this chunk mention any specific named person? Return only JSON with "
    "has_person boolean and confidence number from 0.0 to 1.0."
)
ValidationVerdict = Literal["keep", "uncertain", "drop"]
ExtractionTierMode = Literal["mini_only", "cascade", "frontier_only"]
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
    connected_name: str
    context: str = ""
    relationship_type: str = "associate"
    confidence_score: float | None = None
    text_evidence: str = ""
    validation_verdict: ValidationVerdict = "keep"


class ExtractedProject(BaseModel):
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
    target_name: str
    relationship_type: str = "associate"
    context: str = ""
    text_evidence: str = Field(default="", max_length=200)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


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
    relationships: list[ExtractedRelationship] = Field(default_factory=list)
    projects: list[ExtractedGraphProject] = Field(default_factory=list)


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
                    confidence_score=0.9,
                    text_evidence=(
                        "Errik Anderson and Daniella Reichstetter worked together on the "
                        "Gyrobike first-year project at Tuck."
                    ),
                )
            )
            projects.append(
                ExtractedProject(
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
                    confidence_score=0.7,
                    text_evidence=f"{raw_page.alum_name} has stored public-page evidence.",
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
        for page in pages:
            entity_id = self._entity_id_for_raw_page(page)
            page_entity_ids[page.id] = entity_id
            pages_by_alum[(page.alum_name, entity_id)].append(page)

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
                "name": raw_page.alum_name,
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
                group_key = (raw_page.alum_name, entity_id)
                page_index, page_total = page_metadata[raw_page.id]
                chunks = chunks_by_page_id[raw_page.id]
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
                                "confidence_score": _confidence_score_from_label(
                                    position.confidence
                                ),
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
                parsed_pages += 1
                emit(
                    ProgressEvent(
                        "page_parsed",
                        {
                            "name": raw_page.alum_name,
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
        class_year = self.store.get_class_year_for_alum(raw_page.alum_name)
        context = {"source": "extracted_from_page"}
        if class_year:
            context["class_year"] = class_year
        with self.store.session() as session:
            entity_id = resolve_or_create(
                raw_page.alum_name,
                session=session,
                context=context,
                embedding_client=self.embedding_client,
            )
            session.commit()
        self.store.set_raw_page_entity(raw_page.id, entity_id)
        raw_page.entity_id = entity_id
        return entity_id


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
    extraction = PageExtraction()
    seen_facts: set[tuple[str, str]] = set()
    seen_connections: set[tuple[str, str, str]] = set()
    seen_projects: set[str] = set()
    for chunk_extraction in chunk_extractions:
        for person in chunk_extraction.people:
            name = person.name.strip()
            if not name:
                continue
            key = ("person", name.casefold())
            if key in seen_facts:
                continue
            seen_facts.add(key)
            extraction.facts.append(
                ExtractedFact(
                    category="person",
                    content=name,
                    confidence=_confidence_label(person.confidence),
                    confidence_score=person.confidence,
                    text_evidence=person.text_evidence,
                )
            )
        for organization in chunk_extraction.organizations:
            name = organization.name.strip()
            if not name:
                continue
            key = ("organization", name.casefold())
            if key in seen_facts:
                continue
            seen_facts.add(key)
            extraction.facts.append(
                ExtractedFact(
                    category="organization",
                    content=name,
                    confidence=_confidence_label(organization.confidence),
                    confidence_score=organization.confidence,
                    text_evidence=organization.text_evidence,
                )
            )
        for relationship in chunk_extraction.relationships:
            connected_name = _connected_name_for_relationship(raw_page, relationship)
            if not connected_name:
                continue
            key = (
                connected_name.casefold(),
                relationship.relationship_type.casefold(),
                relationship.text_evidence.casefold(),
            )
            if key in seen_connections:
                continue
            seen_connections.add(key)
            extraction.connections.append(
                ExtractedConnection(
                    connected_name=connected_name,
                    context=relationship.context or relationship.text_evidence,
                    relationship_type=relationship.relationship_type or "associate",
                    confidence_score=relationship.confidence,
                    text_evidence=relationship.text_evidence,
                )
            )
        for project in chunk_extraction.projects:
            project_name = project.project_name.strip()
            if not project_name or project_name.casefold() in seen_projects:
                continue
            seen_projects.add(project_name.casefold())
            extraction.projects.append(
                ExtractedProject(
                    project_name=project_name,
                    description=project.description,
                    confidence_score=project.confidence,
                    text_evidence=project.text_evidence,
                )
            )
            if _project_mentions_alum(raw_page, project):
                connection_key = (
                    project_name.casefold(),
                    "worked_on_project",
                    project.text_evidence.casefold(),
                )
                if connection_key not in seen_connections:
                    seen_connections.add(connection_key)
                    extraction.connections.append(
                        ExtractedConnection(
                            connected_name=project_name,
                            context=project.description or project.text_evidence,
                            relationship_type="worked_on_project",
                            confidence_score=project.confidence,
                            text_evidence=project.text_evidence,
                        )
                    )
    return extraction


def merge_chunk_extractions(
    primary: ChunkExtraction,
    secondary: ChunkExtraction,
) -> ChunkExtraction:
    merged = ChunkExtraction(
        people=[*primary.people],
        organizations=[*primary.organizations],
        relationships=[*primary.relationships],
        projects=[*primary.projects],
    )
    _append_unique(merged.people, secondary.people, lambda item: item.name.casefold())
    _append_unique(merged.organizations, secondary.organizations, lambda item: item.name.casefold())
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
