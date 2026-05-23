from __future__ import annotations

import re
from dataclasses import dataclass

CANONICAL_RELATIONSHIP_TYPES = {
    "advised",
    "classmate",
    "co_worked_at",
    "co_worked_on",
    "educated_at",
    "faculty",
    "founded",
    "led_program",
    "partnered_with",
    "received_grant_from",
    "related_to",
    "spoke_at",
    "taught",
    "worked_at",
    "worked_on_project",
    "worked_with",
}

_EXACT_MAP = {
    "advisor supporter": "advised",
    "advisor mentor and professor": "advised",
    "chief financial officer at": "worked_at",
    "faculty": "faculty",
    "former general partner at": "worked_at",
    "founder and ceo of": "founded",
    "founding faculty director of": "faculty",
    "instructor": "taught",
    "joined as executive director of entrepreneurship": "worked_at",
    "joined as program manager": "worked_at",
    "joined founding team at": "worked_at",
    "partnered with": "partnered_with",
    "professor": "taught",
    "program leader": "led_program",
    "project collaborator": "worked_on_project",
    "research associate at": "worked_at",
    "succeeded as faculty director of": "faculty",
    "welcomed speaker from": "spoke_at",
    "worked on project": "worked_on_project",
    "worked with": "worked_with",
    "worked_on_project": "worked_on_project",
}


@dataclass(frozen=True)
class NormalizedRelationshipType:
    relationship_type: str
    derivation: str = ""


def normalize_relationship_type(value: str) -> NormalizedRelationshipType:
    original = str(value or "").strip()
    if not original:
        return NormalizedRelationshipType("related_to")
    if original in CANONICAL_RELATIONSHIP_TYPES or _is_typed_inference(original):
        return NormalizedRelationshipType(original)

    token = _relationship_token(original)
    if token.startswith("partnered with to "):
        return NormalizedRelationshipType(
            "partnered_with",
            derivation=re.sub(
                r"^partnered\s+with\s+",
                "",
                original,
                flags=re.IGNORECASE,
            ).strip(),
        )
    if token.startswith("partnered with "):
        return NormalizedRelationshipType(
            "partnered_with",
            derivation=original[len("partnered with ") :].strip(),
        )
    if token.startswith("received grant from"):
        return NormalizedRelationshipType("received_grant_from")
    if token.startswith("came to tuck from"):
        return NormalizedRelationshipType("educated_at", derivation=original)

    mapped = _EXACT_MAP.get(token)
    if mapped is not None:
        derivation = "" if mapped == token else original if mapped == "related_to" else ""
        return NormalizedRelationshipType(mapped, derivation=derivation)

    return NormalizedRelationshipType("related_to", derivation=original)


def _relationship_token(value: str) -> str:
    normalized = value.replace("_", " ").replace("/", " ")
    normalized = re.sub(r"[^a-zA-Z0-9']+", " ", normalized).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _is_typed_inference(value: str) -> bool:
    return value.startswith(("co_worked_on:", "co_worked_at:", "classmate:"))
