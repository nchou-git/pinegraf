from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.config import get_settings
from backend.db.models import Claim, ClaimRaw, Document, Fetch
from backend.parse.orchestrator import run_full_parse


@pytest.mark.asyncio
async def test_parse_stores_document_temporal_fields_and_raw_claims(store, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    source = store.upsert_source(kind="domain", identifier="temporal.example")
    first_crawl = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    first_fetch = store.add_fetch(
        source_run_id=first_crawl.id,
        url="https://temporal.example/profile",
        body_bytes=b"<html><main>Errik Anderson works at Acme Corp.</main></html>",
        http_status=200,
        content_type="text/html",
    )
    first_snapshot = datetime(2026, 1, 1, tzinfo=UTC)
    first_parse = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="running",
    )
    with store.session() as session:
        session.get(Fetch, first_fetch.id).fetched_at = first_snapshot
        session.commit()

    await run_full_parse(
        source.id,
        store=store,
        progress_run_id=first_parse.id,
        snapshot_at=first_snapshot,
    )
    store.update_source_run(first_parse.id, status="complete", finished=True)

    second_crawl = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    second_fetch = store.add_fetch(
        source_run_id=second_crawl.id,
        url="https://temporal.example/profile",
        body_bytes=b"<html><main>Errik Anderson works at Beta Corp.</main></html>",
        http_status=200,
        content_type="text/html",
    )
    second_snapshot = datetime(2026, 2, 1, tzinfo=UTC)
    second_parse = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="running",
    )
    with store.session() as session:
        session.get(Fetch, second_fetch.id).fetched_at = second_snapshot
        session.commit()

    await run_full_parse(
        source.id,
        store=store,
        progress_run_id=second_parse.id,
        snapshot_at=second_snapshot,
    )
    store.update_source_run(second_parse.id, status="complete", finished=True)
    get_settings.cache_clear()

    with store.session() as session:
        documents = list(
            session.execute(
                select(Document)
                .where(Document.canonical_url == "https://temporal.example/profile")
                .order_by(Document.valid_from.asc())
            ).scalars()
        )
        raw_claims = list(
            session.execute(
                select(ClaimRaw, Document)
                .join(Document, ClaimRaw.document_id == Document.id)
                .where(ClaimRaw.predicate == "employed_by")
                .order_by(Document.valid_from.asc())
            ).all()
        )
        promoted_claim_count = len(list(session.execute(select(Claim)).scalars()))

    assert [document.valid_from for document in documents] == [first_snapshot, second_snapshot]
    assert [document.valid_to for document in documents] == [None, None]
    assert [document.superseded_by_document_id for document in documents] == [None, None]
    assert len({document.id for document in documents}) == 2

    assert [document.valid_from for _, document in raw_claims] == [first_snapshot, second_snapshot]
    assert [claim.object_text for claim, _ in raw_claims] == ["Acme Corp.", "Beta Corp."]
    assert len({claim.id for claim, _ in raw_claims}) == 2
    assert promoted_claim_count == 0
