from __future__ import annotations

import asyncio
import logging
import os
import uuid

from backend.db.store import Store
from backend.ingestion.orchestrator import run_source_run
from backend.pipeline.orchestrator import run_full_pipeline

PROJECT_ID = "pinegraf-prod"
REGION = "us-east4"
JOB_BY_MODE = {
    "crawl": "pinegraf-crawl",
    "pipeline": "pinegraf-pipeline",
}


async def execute_cloud_run_job(run_id: uuid.UUID | str, mode: str) -> None:
    if mode not in JOB_BY_MODE:
        raise ValueError(f"unsupported PINEGRAF_MODE: {mode}")
    await asyncio.to_thread(_execute_cloud_run_job, uuid.UUID(str(run_id)), mode)


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
    if mode == "pipeline":
        db.update_source_run(run_id, status="running", clear_finished=True)
        pipeline_source_run_id = run.spec.get("pipeline_source_run_id") if run.spec else None
        try:
            await run_full_pipeline(
                pipeline_source_run_id or run_id,
                store=db,
                progress_run_id=run_id,
            )
        except Exception:
            db.update_source_run(run_id, status="failed", finished=True)
            raise
        db.update_source_run(run_id, status="complete", finished=True)
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


def _execute_cloud_run_job(run_id: uuid.UUID, mode: str) -> None:
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
    client.run_job(request=request)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
