from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from backend.db.store import Store

PROGRESS_KEYS = {"stage", "status", "message", "percent"}
TERMINAL_RUN_STATUSES = {"complete", "failed", "partial", "cancelled", "paused"}


@dataclass
class ProgressEvent:
    stage: str
    status: str
    message: str
    percent: float
    data: dict[str, object] = field(default_factory=dict)


def progress_stats(
    stats: dict[str, object],
    *,
    stage: str,
    status: str,
    message: str,
    percent: float,
    data: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = dict(stats)
    payload.update(
        {
            "stage": stage,
            "status": status,
            "message": message,
            "percent": round(max(0.0, min(100.0, float(percent))), 1),
        }
    )
    if data:
        payload.update(data)
    return payload


def has_progress(run_id: uuid.UUID | str, *, store: Store) -> bool:
    run = store.get_source_run(uuid.UUID(str(run_id)))
    return bool(run and run.status == "running")


async def subscribe(
    run_id: uuid.UUID | str,
    *,
    store: Store,
    poll_seconds: float = 0.5,
) -> AsyncIterator[ProgressEvent]:
    run_uuid = uuid.UUID(str(run_id))
    last_signature: tuple[object, ...] | None = None
    while True:
        run = store.get_source_run(run_uuid)
        if run is None:
            yield ProgressEvent("run", "failed", "run not found", 100.0)
            return
        event = _event_from_run(run)
        signature = (
            run.status,
            run.finished_at.isoformat() if run.finished_at else None,
            repr(sorted((run.stats or {}).items())),
            run.error_message,
        )
        if signature != last_signature:
            last_signature = signature
            yield event
        if run.status in TERMINAL_RUN_STATUSES or event.status in {"complete", "failed"}:
            return
        await asyncio.sleep(poll_seconds)


def _event_from_run(run: object) -> ProgressEvent:
    stats = dict(getattr(run, "stats", None) or {})
    row_status = str(getattr(run, "status", "running") or "running")
    event_status = str(stats.get("status") or row_status)
    if row_status in TERMINAL_RUN_STATUSES and event_status not in {"complete", "failed"}:
        event_status = "failed" if row_status == "failed" else "complete"
    percent = stats.get("percent", 100.0 if row_status in TERMINAL_RUN_STATUSES else 0.0)
    try:
        percent_value = float(percent)
    except (TypeError, ValueError):
        percent_value = 0.0
    message = str(stats.get("message") or getattr(run, "error_message", None) or row_status)
    stage = str(stats.get("stage") or getattr(run, "kind", None) or "run")
    data = {key: value for key, value in stats.items() if key not in PROGRESS_KEYS}
    return ProgressEvent(stage, event_status, message, round(percent_value, 1), data)
