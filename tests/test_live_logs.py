from __future__ import annotations

import pytest

from backend.live_logs import append_log, subscribe_logs


@pytest.mark.asyncio
async def test_live_logs_are_read_from_database(store) -> None:
    append_log("info", "crawl started", store=store)

    stream = subscribe_logs(store=store, poll_seconds=0.01)
    try:
        line = await anext(stream)
    finally:
        await stream.aclose()

    assert line["level"] == "INFO"
    assert line["message"] == "crawl started"
    assert line["source_run_id"] is None
