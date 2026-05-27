from __future__ import annotations

import pytest
from claim_helpers import create_claim_graph

from backend.db.store import content_digest
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


@pytest.mark.asyncio
async def test_ask_uses_lexical_chunk_hits_and_cites_originating_url(store) -> None:
    source = store.upsert_source(kind="domain", identifier="tuck.example")
    run = store.create_source_run(source_id=source.id, kind="sitemap", spec={}, triggered_by="test")
    body = b"Daniella Example was the founder and CEO of Gyrobike."
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://tuck.example/faculty",
        body_bytes=body,
        http_status=200,
    )
    document = store.create_document_with_chunks(
        content_hash=content_digest(body),
        cleaned_text=body.decode(),
        title="Faculty",
        canonical_url="https://tuck.example/faculty",
        language="en",
        word_count=8,
        first_seen_fetch_id=fetch.id,
        chunks=[(body.decode(), 8, None)],
    )

    _settings, chunks, claims, citations = await _answer_materials(
        store,
        "Who worked on Gyrobike?",
        max_results=5,
    )

    assert not claims
    assert chunks[0].document_id == document.id
    assert citations[0]["document_url"] == "https://tuck.example/faculty"
    assert "Gyrobike" in citations[0]["quote"]
