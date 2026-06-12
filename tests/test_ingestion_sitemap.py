from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models import Fetch, SourceRun
from backend.ingestion.runners import sitemap as sitemap_runner
from backend.ingestion.runners.sitemap import run_sitemap


@pytest.mark.asyncio
async def test_sitemap_runner_fetches_urls_and_writes_rows(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"sitemap_url": "https://example.com/sitemap.xml"},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/sitemap.xml": fake_httpx.Response(
            "https://example.com/sitemap.xml",
            200,
            b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/a</loc></url>
              <url><loc>https://example.com/b</loc></url>
            </urlset>
            """,
        ),
        "https://example.com/a": fake_httpx.Response(
            "https://example.com/a", 200, b"<html>A</html>"
        ),
        "https://example.com/b": fake_httpx.Response(
            "https://example.com/b", 200, b"<html>B</html>"
        ),
    }

    stats = await run_sitemap(run.id, "https://example.com/sitemap.xml", store=store)

    assert stats["fetched"] == 2
    assert stats["errors"] == 0
    with store.session() as session:
        urls = list(session.execute(select(Fetch.url).order_by(Fetch.url)).scalars())
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert store.get_source_run(run.id).status == "complete"


@pytest.mark.asyncio
async def test_sitemap_runner_follows_links_on_retrieved_pages(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": "https://example.com/sitemap.xml"},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/sitemap.xml": fake_httpx.Response(
            "https://example.com/sitemap.xml",
            200,
            b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/seed</loc></url>
            </urlset>""",
        ),
        "https://example.com/seed": fake_httpx.Response(
            "https://example.com/seed",
            200,
            b"""<html><body>
                <a href="/discovered">in-scope</a>
                <a href="https://other.com/out">out-of-scope</a>
                <a href="mailto:x@y.com">non-http</a>
                <a href="/file.pdf">document</a>
                <a href="/discovered?utm_source=foo">duplicate</a>
            </body></html>""",
        ),
        "https://example.com/discovered": fake_httpx.Response(
            "https://example.com/discovered",
            200,
            b"<html>leaf</html>",
        ),
        "https://example.com/file.pdf": fake_httpx.Response(
            "https://example.com/file.pdf",
            200,
            b"%PDF-1.7",
            headers={"content-type": "application/pdf"},
        ),
    }

    stats = await run_sitemap(run.id, "https://example.com/sitemap.xml", store=store)

    assert stats["fetched"] == 3
    assert stats["errors"] == 0
    with store.session() as session:
        urls = sorted(session.execute(select(Fetch.url)).scalars())
    assert urls == [
        "https://example.com/discovered",
        "https://example.com/file.pdf",
        "https://example.com/seed",
    ]


@pytest.mark.asyncio
async def test_sitemap_runner_depth_one_fetches_only_seed_url(store, fake_httpx) -> None:
    source = store.upsert_source(
        kind="domain",
        identifier="https://example.com/seed",
        crawl_depth=1,
    )
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": source.identifier},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/seed": fake_httpx.Response(
            "https://example.com/seed",
            200,
            b'<html><a href="/next">next</a></html>',
        ),
        "https://example.com/next": fake_httpx.Response(
            "https://example.com/next",
            200,
            b"<html>next</html>",
        ),
    }

    stats = await run_sitemap(run.id, source.identifier, store=store)

    assert stats["fetched"] == 1
    with store.session() as session:
        urls = sorted(session.execute(select(Fetch.url)).scalars())
    assert urls == ["https://example.com/seed"]


@pytest.mark.asyncio
async def test_sitemap_runner_depth_two_fetches_one_link_level(store, fake_httpx) -> None:
    source = store.upsert_source(
        kind="domain",
        identifier="https://example.com/seed",
        crawl_depth=2,
    )
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": source.identifier},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/seed": fake_httpx.Response(
            "https://example.com/seed",
            200,
            b'<html><a href="/next">next</a></html>',
        ),
        "https://example.com/next": fake_httpx.Response(
            "https://example.com/next",
            200,
            b'<html><a href="/third">third</a></html>',
        ),
        "https://example.com/third": fake_httpx.Response(
            "https://example.com/third",
            200,
            b"<html>third</html>",
        ),
    }

    stats = await run_sitemap(run.id, source.identifier, store=store)

    assert stats["fetched"] == 2
    with store.session() as session:
        urls = sorted(session.execute(select(Fetch.url)).scalars())
    assert urls == ["https://example.com/next", "https://example.com/seed"]


@pytest.mark.asyncio
async def test_sitemap_runner_default_depth_still_crawls_fully(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": "https://example.com/seed"},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/seed": fake_httpx.Response(
            "https://example.com/seed",
            200,
            b'<html><a href="/next">next</a></html>',
        ),
        "https://example.com/next": fake_httpx.Response(
            "https://example.com/next",
            200,
            b'<html><a href="/third">third</a></html>',
        ),
        "https://example.com/third": fake_httpx.Response(
            "https://example.com/third",
            200,
            b"<html>third</html>",
        ),
    }

    stats = await run_sitemap(run.id, "https://example.com/seed", store=store)

    assert stats["fetched"] == 3
    with store.session() as session:
        urls = sorted(session.execute(select(Fetch.url)).scalars())
    assert urls == [
        "https://example.com/next",
        "https://example.com/seed",
        "https://example.com/third",
    ]


@pytest.mark.asyncio
async def test_sitemap_runner_follows_subdomain_links(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="tuck.dartmouth.edu")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": "tuck.dartmouth.edu"},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://tuck.dartmouth.edu/robots.txt": fake_httpx.Response(
            "https://tuck.dartmouth.edu/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://mba.tuck.dartmouth.edu/robots.txt": fake_httpx.Response(
            "https://mba.tuck.dartmouth.edu/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://tuck.dartmouth.edu/": fake_httpx.Response(
            "https://tuck.dartmouth.edu/",
            200,
            b'<html><a href="https://mba.tuck.dartmouth.edu/page">mba</a></html>',
        ),
        "https://mba.tuck.dartmouth.edu/page": fake_httpx.Response(
            "https://mba.tuck.dartmouth.edu/page",
            200,
            b"<html>mba</html>",
        ),
    }

    stats = await run_sitemap(run.id, "tuck.dartmouth.edu", store=store)

    assert stats["fetched"] == 2
    assert stats["errors"] == 0
    with store.session() as session:
        urls = sorted(session.execute(select(Fetch.url)).scalars())
    assert "https://mba.tuck.dartmouth.edu/page" in urls


@pytest.mark.asyncio
async def test_sitemap_runner_does_not_auto_queue_parse_on_completion(
    store,
    fake_httpx,
    monkeypatch,
) -> None:
    queued: list[tuple[str, str]] = []

    async def fake_execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((str(run_id), mode))

    from backend.jobs import run as jobs_run

    monkeypatch.setattr(jobs_run, "execute_cloud_run_job", fake_execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": "https://example.com/sitemap.xml"},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/sitemap.xml": fake_httpx.Response(
            "https://example.com/sitemap.xml",
            200,
            b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/a</loc></url>
            </urlset>""",
        ),
        "https://example.com/a": fake_httpx.Response(
            "https://example.com/a", 200, b"<html>A</html>"
        ),
    }

    await run_sitemap(run.id, "https://example.com/sitemap.xml", store=store)

    with store.session() as session:
        parse_runs = session.execute(select(SourceRun).where(SourceRun.kind == "parse")).all()
    assert parse_runs == []
    assert queued == []


def test_host_pacing_exponential_growth_with_cap(monkeypatch) -> None:
    monkeypatch.setattr(sitemap_runner.random, "uniform", lambda low, high: 1.0)
    pacing = sitemap_runner._HostPacing()

    delays = []
    for _ in range(5):
        pacing.record_status(429)
        delays.append(round(pacing.delay_seconds, 3))

    assert delays == [5.0, 10.0, 20.0, 40.0, 80.0]

    pacing.delay_seconds = 299
    pacing.record_status(429)
    assert pacing.delay_seconds == 300.0

    pacing.delay_seconds = 5
    pacing.record_status(429, retry_after_seconds=45)
    assert pacing.delay_seconds == 45
