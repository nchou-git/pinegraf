from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.db.models import Chunk, Document, DocumentFetch, Fetch
from backend.db.store import utc_now
from backend.normalization import normalizer
from backend.normalization.cleaner import clean_html


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
    async def fake_embed(text: str) -> list[float]:
        return [0.0] * 1536

    monkeypatch.setattr(normalizer, "embed_text", fake_embed)

    first_document_id = await normalizer.normalize_fetch(first.id, store=store)
    second_document_id = await normalizer.normalize_fetch(second.id, store=store)

    assert first_document_id == second_document_id
    with store.session() as session:
        document_count = session.execute(select(func.count()).select_from(Document)).scalar_one()
        link_count = session.execute(select(func.count()).select_from(DocumentFetch)).scalar_one()
    assert document_count == 1
    assert link_count == 2


def test_clean_html_removes_nul_bytes() -> None:
    cleaned, title = clean_html(b"<html><title>A\x00B</title><body>One\x00Two</body></html>")

    assert "\x00" not in cleaned
    assert title is None or "\x00" not in title


def test_pending_fetch_ids_are_source_scoped_and_snapshot_filtered(store) -> None:
    source = store.upsert_source(kind="domain", identifier="scope.example")
    first_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    second_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    other_source = store.upsert_source(kind="domain", identifier="other.example")
    other_run = store.create_source_run(
        source_id=other_source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
    )
    previous_unparsed = store.add_fetch(
        source_run_id=first_run.id,
        url="https://scope.example/previous",
        body_bytes=b"previous",
    )
    current_unparsed = store.add_fetch(
        source_run_id=second_run.id,
        url="https://scope.example/current",
        body_bytes=b"current",
    )
    future_unparsed = store.add_fetch(
        source_run_id=second_run.id,
        url="https://scope.example/future",
        body_bytes=b"future",
    )
    parsed = store.add_fetch(
        source_run_id=first_run.id,
        url="https://scope.example/parsed",
        body_bytes=b"parsed",
    )
    store.add_fetch(
        source_run_id=other_run.id,
        url="https://other.example/unparsed",
        body_bytes=b"other",
    )
    document = store.create_document(
        content_hash=b"p" * 32,
        cleaned_text="parsed",
        title="Parsed",
        canonical_url="https://scope.example/parsed",
        language="en",
        word_count=1,
        first_seen_fetch_id=parsed.id,
    )
    store.link_document_fetch(document.id, parsed.id)

    snapshot_at = utc_now()
    with store.session() as session:
        session.get(Fetch, previous_unparsed.id).fetched_at = snapshot_at - timedelta(minutes=2)
        session.get(Fetch, current_unparsed.id).fetched_at = snapshot_at - timedelta(minutes=1)
        session.get(Fetch, parsed.id).fetched_at = snapshot_at - timedelta(minutes=1)
        session.get(Fetch, future_unparsed.id).fetched_at = snapshot_at + timedelta(minutes=1)
        session.commit()

    pending = store.pending_fetch_ids(source_id=source.id, snapshot_at=snapshot_at)

    assert pending == [previous_unparsed.id, current_unparsed.id]


def test_create_document_returns_existing_document_on_hash_race(store) -> None:
    source = store.upsert_source(kind="domain", identifier="race.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
    )
    first = store.add_fetch(
        source_run_id=run.id,
        url="https://race.example/one",
        body_bytes=b"Same",
    )
    second = store.add_fetch(
        source_run_id=run.id,
        url="https://race.example/two",
        body_bytes=b"Same",
    )
    digest = b"1" * 32

    created = store.create_document(
        content_hash=digest,
        cleaned_text="Same",
        title="One",
        canonical_url="https://race.example/one",
        language="en",
        word_count=1,
        first_seen_fetch_id=first.id,
    )
    existing = store.create_document(
        content_hash=digest,
        cleaned_text="Same",
        title="Two",
        canonical_url="https://race.example/two",
        language="en",
        word_count=1,
        first_seen_fetch_id=second.id,
    )

    assert existing.id == created.id
    with store.session() as session:
        document_count = session.execute(select(func.count()).select_from(Document)).scalar_one()
    assert document_count == 1


def test_create_document_with_chunks_legacy_helper_does_not_write_chunks(store) -> None:
    source = store.upsert_source(kind="domain", identifier="legacy.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://legacy.example",
        body_bytes=b"Legacy",
    )

    store.create_document_with_chunks(
        content_hash=b"2" * 32,
        cleaned_text="Legacy",
        title="Legacy",
        canonical_url="https://legacy.example",
        language="en",
        word_count=1,
        first_seen_fetch_id=fetch.id,
        chunks=[("Legacy", 1, [0.0] * 1536)],
    )

    with store.session() as session:
        chunk_count = session.execute(select(func.count()).select_from(Chunk)).scalar_one()
    assert chunk_count == 0
