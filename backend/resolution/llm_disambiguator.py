from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from openai import AsyncOpenAI

from backend.config import get_settings


@dataclass(frozen=True)
class ExtractedMention:
    text: str
    type: str
    qualifiers: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class EntityCandidate:
    entity_id: uuid.UUID
    name: str
    kind: str
    aliases: list[str]
    document_count: int
    last_seen_at: datetime | None
    similarity: float
    qualifiers: dict[str, list[str]]


@dataclass(frozen=True)
class DisambiguationResult:
    entity_id: uuid.UUID | None
    confidence: float
    reasoning: str


async def disambiguate(
    mention: ExtractedMention,
    candidates: list[EntityCandidate],
    context_chunk: str,
) -> DisambiguationResult:
    settings = get_settings()
    if not settings.openai_api_key:
        return DisambiguationResult(None, 0.0, "LLM disambiguation skipped: no API key")
    prompt = _prompt(mention, candidates, context_chunk)
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.cheap_model,
        messages=[
            {
                "role": "system",
                "content": "Resolve ambiguous entity mentions. Return only valid JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return DisambiguationResult(None, 0.0, "LLM returned invalid JSON")
    match = payload.get("match")
    confidence = _confidence(payload.get("confidence"))
    reasoning = str(payload.get("reasoning") or "")
    if isinstance(match, int) and 1 <= match <= len(candidates):
        return DisambiguationResult(candidates[match - 1].entity_id, confidence, reasoning)
    return DisambiguationResult(None, confidence, reasoning or "LLM chose new entity")


def _prompt(
    mention: ExtractedMention,
    candidates: list[EntityCandidate],
    context_chunk: str,
) -> str:
    candidate_lines = []
    for index, candidate in enumerate(candidates, start=1):
        aliases = f"aliases: {candidate.aliases}" if candidate.aliases else "no aliases"
        last_seen = candidate.last_seen_at.date().isoformat() if candidate.last_seen_at else "never"
        candidate_lines.append(
            (
                f'{index}. "{candidate.name}" - {candidate.kind}, {aliases}, '
                f"appears in {candidate.document_count} documents, last seen {last_seen}; "
                f"qualifiers: {_format_qualifiers(candidate.qualifiers)}"
            )
        )
    mention_qualifiers = _format_qualifiers(mention.qualifiers or {})
    return (
        "You are resolving an entity mention to one of N candidate entities.\n"
        f'Mention: "{mention.text}"  (type: {mention.type})\n'
        f"Mention qualifiers: {mention_qualifiers}\n"
        f'Surrounding context: "{context_chunk}"\n\n'
        "Candidates:\n"
        + "\n".join(candidate_lines)
        + "\n\nAre any of these the same entity as the mention? If yes, which number? "
        'If no, say "new entity". Consider name typos, abbreviations, transliterations.\n\n'
        "Rules:\n"
        "- Two people with similar names but different class years, employers, or locations "
        "are different people, even if names match exactly.\n"
        "- Two people with the same class year and overlapping affiliations are likely the "
        "same person, even if names differ slightly.\n"
        "- When in doubt, do NOT merge. Create a new entity. Splitting is fixable later; "
        "over-merging silently corrupts the graph.\n"
        "- Treat class_year, affiliated_with, employed_by, located_in, founded, and "
        "worked_on_project history as high-signal qualifiers.\n\n"
        'Respond with JSON: {"match": <number or null>, "confidence": <0-1>, "reasoning": "..."}'
    )


def _format_qualifiers(qualifiers: dict[str, list[str]]) -> str:
    if not qualifiers:
        return "none"
    parts = []
    for key in sorted(qualifiers):
        values = ", ".join(str(value) for value in qualifiers[key][:5])
        parts.append(f"{key}: {values}")
    return "; ".join(parts)


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
