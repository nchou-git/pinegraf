from __future__ import annotations

import uuid

from backend.corroboration.runner import corroborate_pending
from backend.db.store import Store
from backend.extraction.runner import extract_pending
from backend.normalization.runner import normalize_pending
from backend.progress import ProgressEvent, emit_progress
from backend.projections.runner import rebuild_projections
from backend.resolution.runner import resolve_pending


async def run_full_pipeline(
    source_run_id: uuid.UUID | str,
    *,
    store: Store,
    progress_run_id: uuid.UUID | str | None = None,
) -> set[uuid.UUID]:
    fetch_run_id = uuid.UUID(str(source_run_id))
    run_id = uuid.UUID(str(progress_run_id or source_run_id))
    touched: set[uuid.UUID] = set()
    stats = dict(store.get_source_run(run_id).stats or {}) if store.get_source_run(run_id) else {}
    try:
        await _emit(run_id, ProgressEvent("normalization", "running", "Normalizing fetches", 0.0))
        documents = await normalize_pending(
            store=store,
            source_run_id=fetch_run_id,
            progress=lambda done, total: _item_progress(
                run_id, "normalization", "Normalizing fetches", done, total, 0.0, 20.0
            ),
        )
        stats["normalized_documents"] = len(documents)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, ProgressEvent("extraction", "running", "Extracting claims", 20.0))
        extractor_runs = await extract_pending(
            store=store,
            progress=lambda done, total: _item_progress(
                run_id, "extraction", "Extracting claims", done, total, 20.0, 55.0
            ),
        )
        stats["extractor_runs"] = [str(value) for value in extractor_runs]
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, ProgressEvent("resolution", "running", "Resolving mentions", 55.0))
        touched.update(
            await resolve_pending(
                store=store,
                progress=lambda done, total: _item_progress(
                    run_id, "resolution", "Resolving mentions", done, total, 55.0, 75.0
                ),
            )
        )
        stats["resolved_entities"] = len(touched)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, ProgressEvent("corroboration", "running", "Promoting claims", 75.0))
        touched_claims = await corroborate_pending(store=store)
        stats["touched_claims"] = len(touched_claims)
        store.update_source_run(run_id, stats=stats)

        await _emit(run_id, ProgressEvent("projection", "running", "Rebuilding projections", 90.0))
        rebuilt = await rebuild_projections(touched or None, store=store)
        stats["projected_entities"] = len(rebuilt)
        store.update_source_run(run_id, stats=stats)

        await _emit(
            run_id, ProgressEvent("complete", "complete", "Pipeline complete", 100.0, stats)
        )
        return rebuilt
    except Exception as exc:
        stats["pipeline_error"] = f"{type(exc).__name__}: {exc}"
        store.update_source_run(run_id, stats=stats, error_message=stats["pipeline_error"])
        await _emit(run_id, ProgressEvent("failed", "failed", stats["pipeline_error"], 100.0))
        raise


async def _item_progress(
    run_id: uuid.UUID,
    stage: str,
    message: str,
    done: int,
    total: int,
    start: float,
    end: float,
) -> None:
    if total <= 0:
        percent = end
    else:
        percent = start + ((end - start) * done / total)
    await _emit(run_id, ProgressEvent(stage, "running", message, percent))


async def _emit(run_id: uuid.UUID, event: ProgressEvent) -> None:
    await emit_progress(run_id, event)
