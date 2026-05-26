from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.db.models import Document, DocumentFetch
from backend.normalization import normalizer
from backend.normalization.chunker import Chunk


@pytest.mark.asyncio
async def test_content_hash_dedup_links_multiple_fetches(store, monkeypatch) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"urls": []},
        triggered_by="test",
    )
    first = store.add_fetch(
        source_run_id=run.id,
        url="https://example.com/one",
        body_bytes=b"<html><title>One</title><main>Same body.</main></html>",
    )
    second = store.add_fetch(
        source_run_id=run.id,
        url="https://example.com/two",
        body_bytes=b"<html><title>One</title><main>Same body.</main></html>",
    )

    monkeypatch.setattr(normalizer, "clean_html", lambda raw: ("Same body.", "One"))
    monkeypatch.setattr(normalizer, "detect_language", lambda text: "en")
    monkeypatch.setattr(normalizer, "chunk_text", lambda text: [Chunk(text=text, token_count=3)])

    async def fake_embed(chunks: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in chunks]

    monkeypatch.setattr(normalizer, "embed_chunks", fake_embed)

    first_document_id = await normalizer.normalize_fetch(first.id, store=store)
    second_document_id = await normalizer.normalize_fetch(second.id, store=store)

    assert first_document_id == second_document_id
    with store.session() as session:
        document_count = session.execute(select(func.count()).select_from(Document)).scalar_one()
        link_count = session.execute(select(func.count()).select_from(DocumentFetch)).scalar_one()
    assert document_count == 1
    assert link_count == 2
