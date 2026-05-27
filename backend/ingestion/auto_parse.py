from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db.models import AuditLog, SourceRun
from backend.db.store import Store
from backend.live_logs import append_log

ACTIVE_RUN_STATUSES = {"queued", "running"}


async def enqueue_parse_after_crawl(
    *,
    store: Store,
    crawl_run_id: uuid.UUID,
    crawl_status: str,
) -> None:
    if crawl_status == "failed" or not get_settings().auto_parse:
        return
    crawl_run = store.get_source_run(crawl_run_id)
    if crawl_run is None:
        return
    if _active_parse_run_exists(store, crawl_run.source_id):
        _record_auto_parse_skipped(store, crawl_run)
        append_log(
            "info",
            f"Skipped auto-parse for {crawl_run_id}: parse already in progress",
        )
        return
    try:
        parse_run = store.create_source_run(
            source_id=crawl_run.source_id,
            kind="parse",
            spec={
                "source_id": str(crawl_run.source_id),
                "scope": "unparsed",
                "snapshot_at": (
                    crawl_run.finished_at.isoformat() if crawl_run.finished_at is not None else None
                ),
            },
            triggered_by="auto",
            status="queued",
            audit_action="run.auto_enqueue_parse",
            audit_actor="system",
            audit_payload={
                "source_id": str(crawl_run.source_id),
                "crawl_run_id": str(crawl_run.id),
            },
        )
    except IntegrityError:
        _record_auto_parse_skipped(store, crawl_run)
        append_log(
            "info",
            f"Skipped auto-parse for {crawl_run_id}: parse already in progress",
        )
        return

    try:
        from backend.jobs.run import execute_cloud_run_job

        await execute_cloud_run_job(parse_run.id, "parse")
    except Exception as exc:  # noqa: BLE001
        store.update_source_run(
            parse_run.id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
        append_log(
            "error",
            f"Failed to queue auto-parse for {crawl_run_id}: {type(exc).__name__}: {exc}",
        )
        return

    append_log("info", f"Auto-parse queued for crawl run {crawl_run_id}")


def _active_parse_run_exists(store: Store, source_id: uuid.UUID) -> bool:
    with store.session() as session:
        return bool(
            session.execute(
                select(SourceRun.id)
                .where(SourceRun.source_id == source_id)
                .where(SourceRun.kind == "parse")
                .where(SourceRun.status.in_(ACTIVE_RUN_STATUSES))
                .limit(1)
            ).scalar_one_or_none()
        )


def _record_auto_parse_skipped(store: Store, crawl_run: SourceRun) -> None:
    with store.session() as session:
        session.add(
            AuditLog(
                action="run.auto_enqueue_parse.skipped",
                target_table="source_runs",
                target_id=str(crawl_run.id),
                actor="system",
                payload={
                    "source_id": str(crawl_run.source_id),
                    "crawl_run_id": str(crawl_run.id),
                    "reason": "parse already in progress",
                },
            )
        )
        session.commit()
