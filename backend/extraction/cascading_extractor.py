from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from backend.config import get_settings
from backend.extraction.cost import estimate_cost
from backend.extraction.prompts import OBJECT_TYPES, PREDICATES, SYSTEM_PROMPT, user_prompt

PROMPT_VERSION = "week2_v1"
LOW_CONFIDENCE_THRESHOLD = 0.6


class ExtractedClaim(BaseModel):
    subject_text: str
    predicate: str
    object_text: str | None = None
    object_type: str | None = None
    qualifiers: dict[str, Any] | None = None
    confidence_internal: float = Field(default=0.5, ge=0, le=1)
    raw_quote: str
    span_start: int | None = None
    span_end: int | None = None


class ExtractionResponse(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    claims: list[ExtractedClaim]
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


async def extract_claims(chunk_text: str) -> ExtractionResult:
    settings = get_settings()
    cheap = settings.cheap_model
    frontier = settings.frontier_model
    first = await _extract_with_model(chunk_text, cheap)
    if _needs_frontier(chunk_text, first.claims):
        second = await _extract_with_model(chunk_text, frontier)
        return ExtractionResult(
            claims=second.claims,
            model=f"{cheap}->{frontier}",
            cost_usd=round(first.cost_usd + second.cost_usd, 6),
            input_tokens=first.input_tokens + second.input_tokens,
            output_tokens=first.output_tokens + second.output_tokens,
        )
    return first


async def _extract_with_model(chunk_text: str, model: str) -> ExtractionResult:
    settings = get_settings()
    if not settings.openai_api_key:
        return _heuristic_extract(chunk_text, model)

    prompt = user_prompt(chunk_text)
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or '{"claims":[]}'
    parsed = _parse_response(content)
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage is not None else _token_count(prompt)
    output_tokens = usage.completion_tokens if usage is not None else _token_count(content)
    return ExtractionResult(
        claims=_valid_claims(parsed.claims),
        model=model,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _heuristic_extract(chunk_text: str, model: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    patterns = [
        (
            r"(?P<s>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)+)"
            r"\s+founded\s+"
            r"(?P<o>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+)*)",
            "founded",
            "org",
        ),
        (
            r"(?P<s>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)+)"
            r"\s+partnered with\s+"
            r"(?P<o>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)+)",
            "partnered_with",
            "person",
        ),
        (
            r"(?P<s>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)+)"
            r"\s+(?:works at|worked at|joined)\s+"
            r"(?P<o>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+)*)",
            "employed_by",
            "org",
        ),
        (
            r"(?P<s>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+)+)"
            r"\s+(?:studied at|graduated from)\s+"
            r"(?P<o>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+)*)",
            "studied_at",
            "org",
        ),
    ]
    for pattern, predicate, object_type in patterns:
        for match in re.finditer(pattern, chunk_text):
            quote = match.group(0)
            claims.append(
                ExtractedClaim(
                    subject_text=match.group("s"),
                    predicate=predicate,
                    object_text=match.group("o"),
                    object_type=object_type,
                    qualifiers={},
                    confidence_internal=0.72,
                    raw_quote=quote,
                    span_start=match.start(),
                    span_end=match.end(),
                )
            )
    input_tokens = _token_count(chunk_text)
    output_tokens = max(1, sum(_token_count(claim.model_dump_json()) for claim in claims))
    return ExtractionResult(
        claims=claims,
        model=model,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _parse_response(content: str) -> ExtractionResponse:
    try:
        data = json.loads(content)
        return ExtractionResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return ExtractionResponse()


def _valid_claims(claims: list[ExtractedClaim]) -> list[ExtractedClaim]:
    return [
        claim
        for claim in claims
        if claim.predicate in PREDICATES
        and (claim.object_type is None or claim.object_type in OBJECT_TYPES)
        and claim.raw_quote.strip()
    ]


def _needs_frontier(chunk_text: str, claims: list[ExtractedClaim]) -> bool:
    if any(claim.confidence_internal < LOW_CONFIDENCE_THRESHOLD for claim in claims):
        return True
    entity_count = len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", chunk_text))
    return entity_count >= 4 and len(claims) < 2


def _token_count(text: str) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))
