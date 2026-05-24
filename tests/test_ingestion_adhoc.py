from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models import Fetch
from backend.ingestion import fetcher
from backend.ingestion.runners.adhoc import run_adhoc


class FakeResponse:
    def __init__(self, url: str, status_code: int, body: bytes) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        raise AssertionError(f"unexpected status failure for {self.url}")


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
async def test_adhoc_runner_fetches_urls_and_writes_rows(store, monkeypatch) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="adhoc",
        spec={"urls": ["https://example.com/a"]},
        triggered_by="test",
    )
    FakeAsyncClient.responses = {
        "https://example.com/robots.txt": FakeResponse(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/a": FakeResponse("https://example.com/a", 200, b"<html>A</html>"),
    }
    fetcher._ROBOTS_CACHE.clear()
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeAsyncClient)

    stats = await run_adhoc(run.id, ["https://example.com/a"], store=store)

    assert stats == {"requested": 1, "fetched": 1, "errors": 0}
    with store.session() as session:
        fetch = session.execute(select(Fetch)).scalar_one()
    assert fetch.url == "https://example.com/a"
    assert fetch.body_bytes == b"<html>A</html>"
    assert store.get_source_run(run.id).status == "complete"
