from __future__ import annotations

from backend.pipeline.relationship_types import normalize_relationship_type


def test_relationship_type_normalizes_predicate_and_derivation() -> None:
    normalized = normalize_relationship_type("partnered with to license invention")

    assert normalized.relationship_type == "partnered_with"
    assert normalized.derivation == "to license invention"


def test_relationship_type_preserves_unknown_predicate_as_derivation() -> None:
    normalized = normalize_relationship_type("served as informal sounding board")

    assert normalized.relationship_type == "related_to"
    assert normalized.derivation == "served as informal sounding board"
