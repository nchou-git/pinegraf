from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db.store import Store
from backend.live_logs import append_log


async def enqueue_pipeline_after_crawl(
    *,
    store: Store,
    crawl_run_id: uuid.UUID,
    crawl_status: str,
) -> None:
    if crawl_status == "failed" or not get_settings().auto_pipeline:
        return
    crawl_run = store.get_source_run(crawl_run_id)
    if crawl_run is None:
        return
    try:
        pipeline_run = store.create_source_run(
            source_id=crawl_run.source_id,
            kind="pipeline",
            spec={
                "source_id": str(crawl_run.source_id),
                "pipeline_source_run_id": str(crawl_run.id),
            },
            triggered_by="auto",
            status="queued",
        )
    except IntegrityError:
        append_log(
            "info",
            f"Skipped auto-pipeline for {crawl_run_id}: source already has an active run",
        )
        return

    try:
        from backend.jobs.run import execute_cloud_run_job

        await execute_cloud_run_job(pipeline_run.id, "pipeline")
    except Exception as exc:  # noqa: BLE001
        store.update_source_run(
            pipeline_run.id,
            status="failed",
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
        append_log(
            "error",
            f"Failed to queue auto-pipeline for {crawl_run_id}: {type(exc).__name__}: {exc}",
        )
        return

    append_log("info", f"Auto-pipeline queued for crawl run {crawl_run_id}")
