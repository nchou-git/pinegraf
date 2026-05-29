from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import tiktoken
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from backend.config import get_settings
from backend.extraction.cost import estimate_cost
from backend.extraction.prompts import (
    ENTITY_TYPES,
    OBJECT_TYPES,
    PREDICATES,
    SYSTEM_PROMPT,
    user_prompt,
)

logger = logging.getLogger(__name__)

PROMPT_VERSION = "week3_v1"
PERSON_PATTERN = r"[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){1,4}"
OBJECT_PATTERN = r"[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+)*"
ORG_PATTERN = (
    r"(?:[Tt]he\s+)?[A-Z][A-Za-z0-9&'.-]+"
    r"(?:\s+(?:of|for|and|the|[A-Z][A-Za-z0-9&'.-]+)){0,10}"
)
TITLE_PATTERN = (
    r"(?:interim\s+|associate\s+|senior\s+|deputy\s+|assistant\s+)?"
    r"(?:dean|professor|lecturer|faculty director|faculty member|director|chair)"
)
TITLE_MATCH = rf"(?i:{TITLE_PATTERN})"
PRONOUNS = frozenset(
    {
        "he",
        "she",
        "they",
        "it",
        "we",
        "i",
        "you",
        "his",
        "her",
        "their",
        "its",
        "our",
        "your",
    }
)
ORG_SUFFIXES = (
    " LLC",
    " L.L.C.",
    " LLC.",
    " Inc",
    " Inc.",
    " Incorporated",
    " Co.",
    " Co",
    " Corp",
    " Corp.",
    " Corporation",
    " Ltd",
    " Ltd.",
    " Limited",
    " LLP",
    " L.L.P.",
    " LP",
    " L.P.",
    " PLLC",
    " PBC",
    " GmbH",
    " AG",
    " S.A.",
    " S.A.S.",
    " S.A.R.L.",
    " N.V.",
    " B.V.",
    " Pty Ltd",
    " Pty. Ltd.",
)
HEADLINE_PREFIXES = ("Meet ", "Introducing ", "About ", "Q&A with ", "A Conversation with ")
NEWS_HEADLINE_TOKENS = frozenset(
    {
        "selloff",
        "sell-off",
        "rally",
        "crash",
        "surge",
        "plunge",
        "rebound",
        "drop",
        "drops",
        "halts",
        "halt",
        "resumes",
        "resume",
        "launches",
        "launch",
        "announces",
        "announce",
        "kicks",
        "kicked",
        "opens",
        "closes",
        "ends",
        "begins",
        "starts",
        "reveals",
        "unveils",
        "tops",
        "beats",
        "misses",
        "gains",
        "loses",
        "posts",
        "reports",
        "breaking",
        "exclusive",
        "update",
    }
)


class ExtractedClaim(BaseModel):
    subject_text: str
    subject_type: str = Field(default="person")
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
    rejected_claims: list[dict[str, Any]] | None = None


async def extract_claims(chunk_text: str) -> ExtractionResult:
    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning(
            "extraction.heuristic_fallback openai_api_key not configured; "
            "using heuristic regex extractor — quality will be poor"
        )
        return _heuristic_extract(chunk_text, settings.extraction_model)

    prompt = user_prompt(chunk_text)
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.extraction_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or '{"claims":[]}'
    parsed = _parse_response(content)
    claims, rejected = _valid_claims(parsed.claims)
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage is not None else _token_count(prompt)
    output_tokens = usage.completion_tokens if usage is not None else _token_count(content)
    return ExtractionResult(
        claims=claims,
        model=settings.extraction_model,
        cost_usd=estimate_cost(settings.extraction_model, input_tokens, output_tokens),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        rejected_claims=rejected,
    )


def _heuristic_extract(chunk_text: str, model: str) -> ExtractionResult:
    claims: list[ExtractedClaim] = []
    rejected: list[dict[str, Any]] = []
    patterns = [
        (
            rf"(?P<s>{PERSON_PATTERN})"
            r"\s+(?:founded|started|launched)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "founded",
            "org",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})\b.{{0,120}}?"
            r"\b(?:founded|started|launched|founding|founder(?: and CEO)? of)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "founded",
            "org",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})"
            r"\s+(?:co-created|created|built|developed|led|worked on)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "worked_on_project",
            "project",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})\b.{{0,120}}?"
            r"\b(?:building|built|co-created|co-creating|created|developed|led|worked on)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "worked_on_project",
            "project",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})"
            r"\s+partnered with\s+"
            rf"(?P<o>{PERSON_PATTERN})",
            "partnered_with",
            "person",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})"
            r"\s+(?:works at|worked at|joined)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "employed_by",
            "org",
        ),
        (
            rf"(?P<s>{PERSON_PATTERN})"
            r"\s+(?:studied at|graduated from)\s+"
            rf"(?P<o>{OBJECT_PATTERN})",
            "studied_at",
            "org",
        ),
    ]
    for pattern, predicate, object_type in patterns:
        for match in re.finditer(pattern, chunk_text):
            quote = match.group(0)
            _append_claim(
                claims,
                rejected,
                subject_text=match.group("s"),
                predicate=predicate,
                object_text=match.group("o"),
                object_type=object_type,
                raw_quote=quote,
                span_start=match.start(),
                span_end=match.end(),
            )
    _extract_role_claims(chunk_text, claims, rejected)
    input_tokens = _token_count(chunk_text)
    output_tokens = max(1, sum(_token_count(claim.model_dump_json()) for claim in claims))
    return ExtractionResult(
        claims=claims,
        model=model,
        cost_usd=estimate_cost(model, input_tokens, output_tokens),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        rejected_claims=rejected,
    )


def _extract_role_claims(
    chunk_text: str,
    claims: list[ExtractedClaim],
    rejected: list[dict[str, Any]],
) -> None:
    role_patterns = [
        rf"(?P<s>{PERSON_PATTERN})\s+(?:is|serves as|served as|was named|was appointed)\s+"
        rf"(?:the\s+|an?\s+)?(?P<t>{TITLE_MATCH})\s+(?P<link>of|at|for)\s+(?P<o>{ORG_PATTERN})",
        rf"(?P<s>{PERSON_PATTERN}),\s+(?:the\s+|an?\s+)?(?P<t>{TITLE_MATCH})\s+"
        rf"(?P<link>of|at|for)\s+(?P<o>{ORG_PATTERN})",
        rf"(?P<s>{PERSON_PATTERN})\s+is\s+(?:a|an)\s+(?P<t>{TITLE_MATCH})\s+"
        rf"(?P<link>at|in)\s+(?P<o>{ORG_PATTERN})",
    ]
    for pattern in role_patterns:
        for match in re.finditer(pattern, chunk_text):
            subject = match.group("s")
            title = _normalize_title(match.group("t"))
            org = _clean_org(match.group("o"))
            quote = match.group(0)
            _append_claim(
                claims,
                rejected,
                subject_text=subject,
                predicate="current_title",
                object_text=title,
                object_type="attribute_value",
                raw_quote=quote,
                span_start=match.start(),
                span_end=match.end(),
            )
            _append_claim(
                claims,
                rejected,
                subject_text=subject,
                predicate=(
                    "affiliated_with"
                    if title.casefold().startswith("faculty director")
                    else "employed_by"
                ),
                object_text=org,
                object_type="org",
                raw_quote=quote,
                span_start=match.start(),
                span_end=match.end(),
            )

    endowed_pattern = (
        rf"(?P<s>{PERSON_PATTERN})\b.{{0,80}}?\b"
        rf"(?P<t>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){{0,5}}\s+"
        rf"Professor\s+of\s+[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){{0,5}})"
    )
    for match in re.finditer(endowed_pattern, chunk_text):
        title = _normalize_title(match.group("t"))
        if title.casefold() in {"director", "chair"}:
            continue
        _append_claim(
            claims,
            rejected,
            subject_text=match.group("s"),
            predicate="current_title",
            object_text=title,
            object_type="attribute_value",
            raw_quote=match.group(0),
            span_start=match.start(),
            span_end=match.end(),
        )


def _append_claim(
    claims: list[ExtractedClaim],
    rejected: list[dict[str, Any]],
    *,
    subject_text: str,
    predicate: str,
    object_text: str | None,
    object_type: str | None,
    raw_quote: str,
    span_start: int | None,
    span_end: int | None,
) -> None:
    subject_text, subject_rewritten = _strip_with_flag(subject_text)
    object_text, object_rewritten = _strip_with_flag(object_text)
    if not is_structurally_valid_name(subject_text, "person"):
        rejected.append(
            _rejection_payload(
                subject_text=subject_text,
                predicate=predicate,
                object_text=object_text,
                object_type=object_type,
                raw_quote=raw_quote,
                reason="invalid_subject",
            )
        )
        return
    if object_text and not is_structurally_valid_name(object_text, object_type or "org"):
        rejected.append(
            _rejection_payload(
                subject_text=subject_text,
                predicate=predicate,
                object_text=object_text,
                object_type=object_type,
                raw_quote=raw_quote,
                reason="invalid_object",
            )
        )
        return
    if subject_rewritten or object_rewritten:
        rejected.append(
            _rejection_payload(
                subject_text=subject_text,
                predicate=predicate,
                object_text=object_text,
                object_type=object_type,
                raw_quote=raw_quote,
                reason="headline_prefix_stripped",
            )
        )
    key = (
        subject_text.strip().casefold(),
        predicate,
        (object_text or "").strip().casefold(),
        span_start,
        span_end,
    )
    existing = {
        (
            claim.subject_text.strip().casefold(),
            claim.predicate,
            (claim.object_text or "").strip().casefold(),
            claim.span_start,
            claim.span_end,
        )
        for claim in claims
    }
    if key in existing:
        return
    claims.append(
        ExtractedClaim(
            subject_text=subject_text.strip(),
            predicate=predicate,
            object_text=object_text.strip() if object_text else object_text,
            object_type=object_type,
            qualifiers={},
            confidence_internal=0.72,
            raw_quote=raw_quote,
            span_start=span_start,
            span_end=span_end,
        )
    )


def strip_headline_prefix(text: str) -> str:
    for prefix in HEADLINE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def is_structurally_valid_name(text: str, kind: str) -> bool:
    text = text.strip()
    if not text or len(text) > 80:
        return False
    if "\n" in text or "\t" in text:
        return False
    if re.search(r"\.\s+[A-Z]", text):
        return False
    if re.search(r"[,;:!?]$", text):
        return False
    tokens = text.split()
    if not tokens:
        return False
    for token in tokens:
        if token.casefold() in PRONOUNS:
            return False
    if kind == "person":
        if len(tokens) < 2 or len(tokens) > 5:
            return False
        for token in tokens:
            if token.casefold() in NEWS_HEADLINE_TOKENS:
                return False
        for token in tokens:
            if not token[:1].isupper():
                return False
            if sum(1 for c in token if c.isalpha()) < max(2, len(token) - 1):
                return False
        for suffix in ORG_SUFFIXES:
            if text.endswith(suffix):
                return False
        return True
    if kind in ("org", "place", "project", "event"):
        if any(not token[:1].isalnum() for token in tokens):
            return False
        return True
    return True


def _strip_with_flag(text: str | None) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    stripped = strip_headline_prefix(text.strip())
    return stripped, stripped != text.strip()


def _rejection_payload(
    *,
    subject_text: str | None,
    predicate: str,
    object_text: str | None,
    object_type: str | None,
    raw_quote: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "subject_text": subject_text,
        "predicate": predicate,
        "object_text": object_text,
        "object_type": object_type,
        "raw_quote": raw_quote,
        "reason": reason,
    }


def _normalize_title(value: str) -> str:
    words = re.sub(r"\s+", " ", value.strip().strip(".,;:")).split(" ")
    small = {"of", "and", "for", "the"}
    return " ".join(
        word.casefold() if word.casefold() in small else word[:1].upper() + word[1:].lower()
        for word in words
    )


def _clean_org(value: str) -> str:
    cleaned = re.split(r"\s+and\s+(?:the\s+)?", value.strip(), maxsplit=1)[0]
    return re.sub(r"^the\s+", "", cleaned.strip(".,;:"), flags=re.IGNORECASE)


def _parse_response(content: str) -> ExtractionResponse:
    try:
        data = json.loads(content)
        return ExtractionResponse.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return ExtractionResponse()


def _valid_claims(
    claims: list[ExtractedClaim],
) -> tuple[list[ExtractedClaim], list[dict[str, Any]]]:
    accepted: list[ExtractedClaim] = []
    rejected: list[dict[str, Any]] = []
    for claim in claims:
        reason = None
        if claim.predicate not in PREDICATES:
            reason = "invalid_predicate"
        elif claim.subject_type not in ENTITY_TYPES:
            reason = "invalid_subject_type"
        elif claim.object_type is not None and claim.object_type not in OBJECT_TYPES:
            reason = "invalid_object_type"
        elif not claim.raw_quote.strip():
            reason = "empty_quote"

        subject_text, subject_rewritten = _strip_with_flag(claim.subject_text)
        object_text, object_rewritten = _strip_with_flag(claim.object_text)
        if reason is None and not is_structurally_valid_name(
            subject_text or "",
            claim.subject_type,
        ):
            reason = "invalid_subject"
        if (
            reason is None
            and object_text
            and not is_structurally_valid_name(object_text, claim.object_type or "org")
        ):
            reason = "invalid_object"
        if reason is not None:
            rejected.append(
                _rejection_payload(
                    subject_text=subject_text,
                    predicate=claim.predicate,
                    object_text=object_text,
                    object_type=claim.object_type,
                    raw_quote=claim.raw_quote,
                    reason=reason,
                )
            )
            continue
        if subject_rewritten or object_rewritten:
            rejected.append(
                _rejection_payload(
                    subject_text=subject_text,
                    predicate=claim.predicate,
                    object_text=object_text,
                    object_type=claim.object_type,
                    raw_quote=claim.raw_quote,
                    reason="headline_prefix_stripped",
                )
            )
        accepted.append(
            claim.model_copy(update={"subject_text": subject_text, "object_text": object_text})
        )
    return accepted, rejected


def _token_count(text: str) -> int:
    encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))
