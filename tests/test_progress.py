from __future__ import annotations

import pytest

from backend.progress import has_progress, progress_stats, subscribe


def test_has_progress_reads_running_source_run_from_database(store) -> None:
    source = store.upsert_source(kind="domain", identifier="progress.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": source.identifier},
        triggered_by="test",
        status="running",
    )

    assert has_progress(run.id, store=store) is True

    store.update_source_run(run.id, status="complete", finished=True)
    assert has_progress(run.id, store=store) is False


@pytest.mark.asyncio
async def test_subscribe_yields_progress_from_source_run_stats(store) -> None:
    source = store.upsert_source(kind="domain", identifier="stream.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": source.identifier},
        triggered_by="test",
        status="running",
    )
    store.update_source_run(
        run.id,
        stats=progress_stats(
            {"fetched": 10, "known": 20},
            stage="crawl",
            status="running",
            message="Retrieving documents",
            percent=50,
        ),
    )

    stream = subscribe(run.id, store=store, poll_seconds=0.01)
    event = await anext(stream)

    assert event.stage == "crawl"
    assert event.status == "running"
    assert event.percent == 50
    assert event.data["fetched"] == 10
    await stream.aclose()
