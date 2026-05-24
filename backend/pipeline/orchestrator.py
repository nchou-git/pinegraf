from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from backend.corroboration.runner import corroborate_pending
from backend.db.store import Store
from backend.extraction.runner import extract_pending
from backend.normalization.runner import normalize_pending
from backend.projections.runner import rebuild_projections
from backend.resolution.runner import resolve_pending


@dataclass
class PipelineEvent:
    stage: str
    status: str
    message: str
    percent: int
    data: dict[str, object] = field(default_factory=dict)


_SUBSCRIBERS: dict[uuid.UUID, list[asyncio.Queue[PipelineEvent]]] = defaultdict(list)


async def run_full_pipeline(
    workspace_id: str,
    source_run_id: uuid.UUID | str,
    *,
    store: Store,
) -> set[uuid.UUID]:
    run_id = _as_uuid(source_run_id)
    touched: set[uuid.UUID] = set()
    stats = dict(store.get_source_run(run_id).stats or {}) if store.get_source_run(run_id) else {}
    try:
        await _emit(run_id, PipelineEvent("normalization", "running", "Normalizing fetches", 10))
        documents = await normalize_pending(store=store, source_run_id=run_id)
        stats["normalized_documents"] = len(documents)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, PipelineEvent("extraction", "running", "Extracting claims", 35))
        extractor_runs = await extract_pending(workspace_id, store=store)
        stats["extractor_runs"] = [str(value) for value in extractor_runs]
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, PipelineEvent("resolution", "running", "Resolving mentions", 60))
        touched.update(await resolve_pending(workspace_id, store=store))
        stats["resolved_entities"] = len(touched)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, PipelineEvent("corroboration", "running", "Promoting claims", 80))
        touched_claims = await corroborate_pending(workspace_id, store=store)
        stats["touched_claims"] = len(touched_claims)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, PipelineEvent("projection", "running", "Rebuilding projections", 92))
        rebuilt = await rebuild_projections(workspace_id, touched or None, store=store)
        stats["projected_entities"] = len(rebuilt)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, PipelineEvent("complete", "complete", "Pipeline complete", 100, stats))
        return rebuilt
    except Exception as exc:
        stats["pipeline_error"] = f"{type(exc).__name__}: {exc}"
        store.update_source_run(run_id, stats=stats, error_message=stats["pipeline_error"])
        await _emit(run_id, PipelineEvent("failed", "failed", stats["pipeline_error"], 100))
        raise


async def subscribe(run_id: uuid.UUID | str) -> AsyncIterator[PipelineEvent]:
    run_uuid = _as_uuid(run_id)
    queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
    _SUBSCRIBERS[run_uuid].append(queue)
    try:
        while True:
            event = await queue.get()
            yield event
            if event.status in {"complete", "failed"}:
                break
    finally:
        _SUBSCRIBERS[run_uuid].remove(queue)


async def _emit(run_id: uuid.UUID, event: PipelineEvent) -> None:
    for queue in list(_SUBSCRIBERS.get(run_id, [])):
        await queue.put(event)


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
