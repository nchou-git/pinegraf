from __future__ import annotations

from backend.test_seed import seed_synthetic_test_data
from backend.web_api import entity_detail, search_entities


def test_synthetic_test_seed_is_idempotent(store) -> None:
    first = seed_synthetic_test_data(store)
    second = seed_synthetic_test_data(store)

    assert first["claims_inserted"] == 13
    assert second["claims_inserted"] == 0

    results = search_entities(store, q="TEST", limit=25)["results"]
    assert len(results) == 10
    names = {row["canonical_name"] for row in results}
    assert "TEST — Ava Example [DEMO]" in names
    assert "TEST — Founders Forum 2099 [DEMO]" in names

    ava_id = next(row["entity_id"] for row in results if "Ava" in row["canonical_name"])
    graph = entity_detail(store, ava_id)
    assert graph is not None
    assert len(graph["connections"]) == 3
    assert any(connection["is_resolved"] is False for connection in graph["connections"])
