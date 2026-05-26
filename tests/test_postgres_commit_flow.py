from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.db.models import Fetch, Source, SourceRun
from backend.ingestion.runners.sitemap import run_sitemap


@pytest.mark.asyncio
async def test_full_crawl_commits_rows_to_postgres(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="commit.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": "https://commit.example/sitemap.xml"},
        triggered_by="test",
        status="queued",
    )
    fake_httpx.responses = {
        "https://commit.example/robots.txt": fake_httpx.Response(
            "https://commit.example/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://commit.example/sitemap.xml": fake_httpx.Response(
            "https://commit.example/sitemap.xml",
            200,
            b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://commit.example/a</loc></url>
            </urlset>""",
        ),
        "https://commit.example/a": fake_httpx.Response(
            "https://commit.example/a",
            200,
            b"<html><main>Committed</main></html>",
        ),
    }

    await run_sitemap(run.id, "https://commit.example/sitemap.xml", store=store)

    with store.session() as session:
        assert session.execute(select(func.count()).select_from(Source)).scalar_one() >= 1
        assert session.execute(select(func.count()).select_from(SourceRun)).scalar_one() >= 1
        assert session.execute(select(func.count()).select_from(Fetch)).scalar_one() >= 1
