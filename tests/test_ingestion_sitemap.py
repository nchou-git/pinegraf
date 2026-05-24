from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models import Fetch
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

    assert stats == {"sitemaps": 1, "discovered": 2, "fetched": 2, "errors": 0}
    with store.session() as session:
        urls = list(session.execute(select(Fetch.url).order_by(Fetch.url)).scalars())
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert store.get_source_run(run.id).status == "complete"
