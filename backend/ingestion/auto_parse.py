from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db.store import Store
from backend.live_logs import append_log


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
    try:
        parse_run = store.create_source_run(
            source_id=crawl_run.source_id,
            kind="parse",
            spec={
                "source_id": str(crawl_run.source_id),
                "parse_source_run_id": str(crawl_run.id),
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
        append_log(
            "info",
            f"Skipped auto-parse for {crawl_run_id}: source already has an active run",
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
