from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.models import Fetch
from backend.ingestion.runners.adhoc import run_adhoc


@pytest.mark.asyncio
async def test_adhoc_runner_fetches_urls_and_writes_rows(store, fake_httpx) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="adhoc",
        spec={"urls": ["https://example.com/a"]},
        triggered_by="test",
    )
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/a": fake_httpx.Response(
            "https://example.com/a", 200, b"<html>A</html>"
        ),
    }
    stats = await run_adhoc(run.id, ["https://example.com/a"], store=store)

    assert stats == {"requested": 1, "fetched": 1, "errors": 0}
    with store.session() as session:
        fetch = session.execute(select(Fetch)).scalar_one()
    assert fetch.url == "https://example.com/a"
    assert fetch.body_bytes == b"<html>A</html>"
    assert store.get_source_run(run.id).status == "complete"
