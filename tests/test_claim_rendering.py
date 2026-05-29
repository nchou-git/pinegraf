from __future__ import annotations

from claim_helpers import create_claim_graph

from backend.web_api import _entity_ref, claim_detail


def test_claim_rendering_resolves_entity_names(store) -> None:
    graph = create_claim_graph(store)

    payload = claim_detail(store, graph["claim"].id)

    assert payload["subject"] == {"id": str(graph["subject"].id), "name": "Erik Snowberg"}
    assert payload["object"] == {
        "id": str(graph["object"].id),
        "name": "Tuck School of Business",
    }
    assert payload["predicate"] == "employed_by"
    assert payload["evidence_count"] == 1


def test_claim_rendering_unknown_entity_fallback() -> None:
    import uuid

    missing = uuid.uuid4()
    assert _entity_ref(None, missing)["name"] == f"Unknown entity {str(missing)[:8]}"
