from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.config import get_settings
from backend.db.models import AuditLog, SourceRun
from backend.jobs import run as jobs_run


@pytest.mark.asyncio
async def test_parse_completion_queues_followup_for_unparsed_crawl_gap(
    store,
    monkeypatch,
) -> None:
    queued: list[tuple[str, str]] = []

    async def fake_run_full_parse(*args, **kwargs) -> set:
        del args, kwargs
        return set()

    async def fake_execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((str(run_id), mode))

    source = store.upsert_source(kind="domain", identifier="gap.example")
    crawl_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id)},
        triggered_by="test",
        status="complete",
    )
    store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://gap.example/new-after-snapshot",
        body_bytes=b"new content",
        http_status=200,
    )
    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="queued",
    )

    monkeypatch.setenv("PINEGRAF_AUTO_PARSE", "true")
    get_settings.cache_clear()
    monkeypatch.setenv("PINEGRAF_RUN_ID", str(parse_run.id))
    monkeypatch.setenv("PINEGRAF_MODE", "parse")
    monkeypatch.setattr(jobs_run, "run_full_parse", fake_run_full_parse)
    monkeypatch.setattr(jobs_run, "execute_cloud_run_job", fake_execute_cloud_run_job)

    await jobs_run.run_from_env(store=store)
    get_settings.cache_clear()

    with store.session() as session:
        parse_runs = list(
            session.execute(
                select(SourceRun).where(SourceRun.source_id == source.id, SourceRun.kind == "parse")
            ).scalars()
        )
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "run.auto_enqueue_parse")
        ).scalar_one()

    assert store.get_source_run(parse_run.id).status == "complete"
    assert len(parse_runs) == 2
    followup = next(run for run in parse_runs if run.id != parse_run.id)
    assert followup.status == "queued"
    assert followup.spec == {"source_id": str(source.id), "scope": "unparsed"}
    assert queued == [(str(followup.id), "parse")]
    assert audit.payload["parse_run_id"] == str(parse_run.id)
    assert audit.payload["pending_fetches"] == 1


@pytest.mark.asyncio
async def test_parse_selected_completion_does_not_queue_whole_source_followup(
    store,
    monkeypatch,
) -> None:
    queued: list[tuple[str, str]] = []

    async def fake_run_full_parse(*args, **kwargs) -> set:
        del args, kwargs
        return set()

    async def fake_execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((str(run_id), mode))

    source = store.upsert_source(kind="domain", identifier="selected.example")
    crawl_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id)},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://selected.example/new",
        body_bytes=b"new content",
        http_status=200,
    )
    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={
            "source_id": str(source.id),
            "scope": "fetch_ids",
            "fetch_ids": [str(fetch.id)],
        },
        triggered_by="test",
        status="queued",
    )

    monkeypatch.setenv("PINEGRAF_AUTO_PARSE", "true")
    get_settings.cache_clear()
    monkeypatch.setenv("PINEGRAF_RUN_ID", str(parse_run.id))
    monkeypatch.setenv("PINEGRAF_MODE", "parse")
    monkeypatch.setattr(jobs_run, "run_full_parse", fake_run_full_parse)
    monkeypatch.setattr(jobs_run, "execute_cloud_run_job", fake_execute_cloud_run_job)

    await jobs_run.run_from_env(store=store)
    get_settings.cache_clear()

    with store.session() as session:
        parse_count = session.execute(
            select(SourceRun).where(SourceRun.source_id == source.id, SourceRun.kind == "parse")
        ).all()

    assert len(parse_count) == 1
    assert queued == []
