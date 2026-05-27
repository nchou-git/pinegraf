from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.db.models import DocumentFetch, Fetch
from backend.db.stats_queries import (
    documents_for_source,
    pages_fetched,
    pending_parse_count,
    urls_known,
)
from backend.db.store import content_digest, utc_now
from backend.ingestion.runners.sitemap import run_sitemap
from backend.normalization import runner as normalization_runner
from backend.parse.orchestrator import run_full_parse
from backend.web_api import list_sources, stats


@pytest.mark.asyncio
async def test_hash_diff_recrawl_marks_unchanged_body_and_keeps_canonical_counts(
    store,
    fake_httpx,
) -> None:
    source = store.upsert_source(kind="domain", identifier="hash.example")

    async def crawl_once() -> None:
        run = store.create_source_run(
            source_id=source.id,
            kind="sitemap",
            spec={"source_input": "https://hash.example/sitemap.xml"},
            triggered_by="test",
            status="running",
        )
        await run_sitemap(run.id, "https://hash.example/sitemap.xml", store=store)

    fake_httpx.responses = {
        "https://hash.example/robots.txt": fake_httpx.Response(
            "https://hash.example/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://hash.example/sitemap.xml": fake_httpx.Response(
            "https://hash.example/sitemap.xml",
            200,
            b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://hash.example/page</loc></url>
            </urlset>""",
        ),
        "https://hash.example/page": fake_httpx.Response(
            "https://hash.example/page",
            200,
            b"<html><main>same body</main></html>",
        ),
    }

    await crawl_once()
    await crawl_once()

    with store.session() as session:
        fetches = list(
            session.execute(
                select(Fetch)
                .where(Fetch.url == "https://hash.example/page")
                .order_by(Fetch.fetched_at)
            ).scalars()
        )
        assert len(fetches) == 2
        assert fetches[0].body_bytes is not None
        assert fetches[1].body_bytes is None
        assert fetches[1].body_unchanged_since == fetches[0].id
        assert pages_fetched(session, source.id) == 1
        assert urls_known(session, source.id) == 1
        assert pending_parse_count(session, source.id) == 1
        assert store.pending_fetch_ids(source_id=source.id) == [fetches[0].id]


@pytest.mark.asyncio
async def test_parse_scope_is_frozen_while_concurrent_crawl_adds_fetch(store, monkeypatch) -> None:
    source = store.upsert_source(kind="domain", identifier="freeze.example")
    crawl_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    first = store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://freeze.example/one",
        body_bytes=b"one",
        http_status=200,
    )
    second = store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://freeze.example/two",
        body_bytes=b"two",
        http_status=200,
    )
    snapshot = utc_now()
    added_mid_run: list[Fetch] = []

    async def fake_normalize_fetch(fetch_id, *, store, valid_from=None):
        fetch = store.get_fetch(fetch_id)
        digest = content_digest(str(fetch_id).encode("utf-8"))
        document = store.create_document_with_chunks(
            content_hash=digest,
            cleaned_text=fetch.url,
            title=None,
            canonical_url=fetch.url,
            language="en",
            word_count=1,
            first_seen_fetch_id=fetch.id,
            chunks=[(fetch.url, 1, None)],
            valid_from=valid_from,
        )
        if not added_mid_run:
            added_mid_run.append(
                store.add_fetch(
                    source_run_id=crawl_run.id,
                    url="https://freeze.example/three",
                    body_bytes=b"three",
                    http_status=200,
                )
            )
        return document.id

    monkeypatch.setattr(normalization_runner, "normalize_fetch", fake_normalize_fetch)
    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="running",
    )

    await run_full_parse(source.id, store=store, progress_run_id=parse_run.id, snapshot_at=snapshot)

    run = store.get_source_run(parse_run.id)
    assert run.stats["total_to_parse"] == 2
    assert run.stats["items_parsed"] == 2
    assert set(store.pending_fetch_ids(source_id=source.id)) == {added_mid_run[0].id}
    with store.session() as session:
        linked = set(session.execute(select(DocumentFetch.fetch_id)).scalars())
    assert linked == {first.id, second.id}


def test_canonical_stats_are_shared_by_sources_and_top_cards(store) -> None:
    source = store.upsert_source(kind="domain", identifier="stats.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://stats.example/page",
        body_bytes=b"stats",
        http_status=200,
    )
    document = store.create_document_with_chunks(
        content_hash=b"s" * 32,
        cleaned_text="stats",
        title=None,
        canonical_url=fetch.url,
        language="en",
        word_count=1,
        first_seen_fetch_id=fetch.id,
        chunks=[("stats", 1, None)],
    )
    store.link_document_fetch(document.id, fetch.id)

    [source_payload] = list_sources(store)
    top_stats = stats(store)
    with store.session() as session:
        assert source_payload["coverage"]["pages_fetched"] == pages_fetched(session, source.id)
        assert source_payload["coverage"]["urls_known"] == urls_known(session, source.id)
        assert source_payload["coverage"]["documents"] == documents_for_source(session, source.id)
    assert top_stats["documents"] == source_payload["coverage"]["documents"]


def test_pending_fetch_ids_match_pending_parse_count(store) -> None:
    source = store.upsert_source(kind="domain", identifier="pending.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    pending = store.add_fetch(
        source_run_id=run.id,
        url="https://pending.example/one",
        body_bytes=b"one",
        http_status=200,
    )
    skipped = store.add_fetch(
        source_run_id=run.id,
        url="https://pending.example/two",
        body_bytes=None,
        content_hash=content_digest(b"one"),
        body_unchanged_since=pending.id,
        http_status=200,
    )
    with store.session() as session:
        session.get(Fetch, skipped.id).fetched_at = utc_now() + timedelta(minutes=1)
        session.commit()
        assert pending_parse_count(session, source.id) == len(
            store.pending_fetch_ids(source_id=source.id)
        )
