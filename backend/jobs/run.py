from __future__ import annotations

import asyncio
import logging
import os
import uuid

from backend.db.store import Store, utc_now
from backend.ingestion.auto_parse import enqueue_parse_after_parse
from backend.ingestion.orchestrator import run_source_run
from backend.parse.orchestrator import run_full_parse

PROJECT_ID = "pinegraf-prod"
REGION = "us-east4"
JOB_BY_MODE = {
    "crawl": "pinegraf-crawl",
    "parse": "pinegraf-parse",
}


async def execute_cloud_run_job(run_id: uuid.UUID | str, mode: str) -> None:
    if mode not in JOB_BY_MODE:
        raise ValueError(f"unsupported PINEGRAF_MODE: {mode}")
    run_uuid = uuid.UUID(str(run_id))
    metadata = await asyncio.to_thread(_execute_cloud_run_job, run_uuid, mode)
    if metadata:
        try:
            Store().patch_source_run_spec(run_uuid, metadata)
        except Exception:  # noqa: BLE001
            logging.warning("Failed to persist Cloud Run metadata for %s", run_uuid, exc_info=True)


async def run_from_env(*, store: Store | None = None) -> None:
    run_id = uuid.UUID(_required_env("PINEGRAF_RUN_ID"))
    mode = _required_env("PINEGRAF_MODE")
    db = store or Store()
    run = db.get_source_run(run_id)
    if run is None:
        raise ValueError(f"source run not found: {run_id}")
    if mode == "crawl":
        await run_source_run(run_id, store=db)
        return
    if mode == "parse":
        db.update_source_run(run_id, status="running", clear_finished=True)
        spec = dict(run.spec or {})
        snapshot_at = spec.get("snapshot_at")
        if not snapshot_at:
            snapshot_at = utc_now().isoformat()
            db.patch_source_run_spec(run_id, {"snapshot_at": snapshot_at})
        try:
            await run_full_parse(
                spec.get("source_id") or run.source_id,
                store=db,
                progress_run_id=run_id,
                scope=str(spec.get("scope") or "unparsed"),
                fetch_ids=list(spec.get("fetch_ids") or []),
                snapshot_at=snapshot_at,
            )
        except Exception:
            current = db.get_source_run(run_id)
            if current is not None and current.status == "stopped":
                return
            db.update_source_run(run_id, status="failed", finished=True)
            raise
        current = db.get_source_run(run_id)
        if current is None or current.status == "stopped":
            return
        db.update_source_run(run_id, status="complete", finished=True)
        await enqueue_parse_after_parse(store=db, parse_run_id=run_id)
        return
    raise ValueError(f"unsupported PINEGRAF_MODE: {mode}")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_from_env())
    except Exception:  # noqa: BLE001
        logging.exception("Pinegraf job failed")
        return 1
    return 0


def cancel_cloud_run_execution(run) -> str:
    from google.cloud import run_v2

    mode = "crawl" if run.kind in {"sitemap", "seed"} else run.kind
    job = JOB_BY_MODE.get(mode)
    if not job:
        raise RuntimeError(f"unsupported Cloud Run job mode for run kind: {run.kind}")

    spec = dict(run.spec or {})
    execution_name = str(spec.get("cloud_run_execution") or "")
    if not execution_name:
        operation_name = str(spec.get("cloud_run_operation") or "")
        if "/executions/" in operation_name:
            execution_name = operation_name
    if not execution_name:
        raise RuntimeError(f"no exact Cloud Run execution recorded for {job}")

    request_type = getattr(run_v2, "CancelExecutionRequest", None)
    request = request_type(name=execution_name) if request_type else {"name": execution_name}
    client_type = getattr(run_v2, "ExecutionsClient", None) or getattr(run_v2, "JobsClient")
    client = client_type()
    client.cancel_execution(request=request)
    return execution_name


def _execute_cloud_run_job(run_id: uuid.UUID, mode: str) -> dict[str, object]:
    from google.cloud import run_v2

    job = JOB_BY_MODE[mode]
    client = run_v2.JobsClient()
    request = run_v2.RunJobRequest(
        name=f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{job}",
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="PINEGRAF_RUN_ID", value=str(run_id)),
                        run_v2.EnvVar(name="PINEGRAF_MODE", value=mode),
                    ]
                )
            ]
        ),
    )
    operation = client.run_job(request=request)
    metadata: dict[str, object] = {
        "cloud_run_job": job,
        "cloud_run_mode": mode,
    }
    operation_name = str(getattr(getattr(operation, "operation", None), "name", "") or "")
    if operation_name:
        metadata["cloud_run_operation"] = operation_name
    operation_metadata = getattr(operation, "metadata", None)
    for field in ("execution", "execution_name", "name"):
        value = str(getattr(operation_metadata, field, "") or "")
        if "/executions/" in value:
            metadata["cloud_run_execution"] = value
            break
    return metadata


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
