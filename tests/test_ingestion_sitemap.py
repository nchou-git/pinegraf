from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import Fetch
from backend.ingestion import fetcher
from backend.ingestion.runners.sitemap import run_sitemap


class FakeResponse:
    def __init__(self, url: str, status_code: int, body: bytes) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        request = httpx.Request("GET", self.url)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("status error", request=request, response=response)


class FakeAsyncClient:
    responses: dict[str, FakeResponse] = {}

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> None:
        del args

    async def get(self, url: str, follow_redirects: bool = True) -> FakeResponse:
        del follow_redirects
        return self.responses[url]


@pytest.mark.asyncio
async def test_sitemap_runner_fetches_urls_and_writes_rows(store, monkeypatch) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"sitemap_url": "https://example.com/sitemap.xml"},
        triggered_by="test",
    )
    FakeAsyncClient.responses = {
        "https://example.com/robots.txt": FakeResponse(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/sitemap.xml": FakeResponse(
            "https://example.com/sitemap.xml",
            200,
            b"""
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/a</loc></url>
              <url><loc>https://example.com/b</loc></url>
            </urlset>
            """,
        ),
        "https://example.com/a": FakeResponse("https://example.com/a", 200, b"<html>A</html>"),
        "https://example.com/b": FakeResponse("https://example.com/b", 200, b"<html>B</html>"),
    }
    fetcher._ROBOTS_CACHE.clear()
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeAsyncClient)

    stats = await run_sitemap(run.id, "https://example.com/sitemap.xml", store=store)

    assert stats == {"sitemaps": 1, "discovered": 2, "fetched": 2, "errors": 0}
    with store.session() as session:
        urls = list(session.execute(select(Fetch.url).order_by(Fetch.url)).scalars())
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert store.get_source_run(run.id).status == "complete"
