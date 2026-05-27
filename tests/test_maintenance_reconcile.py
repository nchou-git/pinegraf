from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from backend.db.models import AuditLog, Fetch, SourceRun
from backend.db.store import utc_now
from backend.maintenance.reconcile import reconcile_all_sources


def test_maintenance_marks_stale_running_run_failed_and_audits(store) -> None:
    source = store.upsert_source(kind="domain", identifier="stale.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id)},
        triggered_by="test",
        status="running",
    )
    with store.session() as session:
        db_run = session.get(SourceRun, run.id)
        db_run.stats_updated_at = utc_now() - timedelta(minutes=31)
        session.commit()

    summary = reconcile_all_sources(store)

    assert summary["stale_runs_failed"] == 1
    assert store.get_source_run(run.id).status == "failed"
    with store.session() as session:
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "run.auto_failed_stale")
        ).scalar_one()
    assert audit.target_id == str(run.id)


def test_maintenance_corrects_drifted_source_counters(store) -> None:
    source = store.upsert_source(kind="domain", identifier="drift.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    store.add_fetch(
        source_run_id=run.id,
        url="https://drift.example/page",
        body_bytes=b"body",
        http_status=200,
    )
    with store.session() as session:
        db_source = session.get(type(source), source.id)
        db_source.pages_fetched_total = 99
        db_source.urls_known_total = 101
        session.commit()

    summary = reconcile_all_sources(store)

    assert summary["stats_counters_corrected"] == 1
    refreshed = store.get_source(source.id)
    assert refreshed.pages_fetched_total == 1
    assert refreshed.urls_known_total == 1


def test_maintenance_flags_broken_body_chain_without_deleting(store) -> None:
    source = store.upsert_source(kind="domain", identifier="broken-chain.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    broken = store.add_fetch(
        source_run_id=run.id,
        url="https://broken-chain.example/page",
        body_bytes=None,
        content_hash=b"b" * 32,
        http_status=200,
    )
    with store.session() as session:
        fetch = session.get(Fetch, broken.id)
        fetch.body_unchanged_since = broken.id
        session.commit()

    summary = reconcile_all_sources(store)

    assert summary["broken_body_chains"] == 1
    assert store.get_fetch(broken.id) is not None
    with store.session() as session:
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "maintenance.broken_body_chain")
        ).scalar_one()
    assert audit.target_id == str(broken.id)
