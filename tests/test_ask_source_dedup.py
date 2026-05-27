from __future__ import annotations

import pytest
from claim_helpers import create_claim_graph

from backend.web_api import _answer_materials


@pytest.mark.asyncio
async def test_ask_sources_are_deduped_by_document(store) -> None:
    graph = create_claim_graph(store)
    second_fetch = store.add_fetch(
        source_run_id=graph["run"].id,
        url="https://claims.example/profile",
        body_bytes=None,
        content_hash=graph["fetch"].content_hash,
        body_unchanged_since=graph["fetch"].id,
        http_status=200,
    )
    store.link_document_fetch(graph["document"].id, second_fetch.id)

    _settings, _chunks, claims, citations = await _answer_materials(
        store,
        "Where does Erik Snowberg work?",
        max_results=10,
    )

    assert claims[0]["subject"]["name"] == "Erik Snowberg"
    assert len(citations) == 1
    assert citations[0]["document_id"] == str(graph["document"].id)
    assert citations[0]["claim"]["object"]["name"] == "Tuck School of Business"
