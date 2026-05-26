from __future__ import annotations

import uuid

from backend.corroboration.runner import corroborate_pending
from backend.db.store import Store
from backend.extraction.runner import extract_pending
from backend.normalization.runner import normalize_pending
from backend.progress import progress_stats
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
    run = store.get_source_run(run_id)
    stats = dict(run.stats or {}) if run else {}
    try:
        _write_progress(store, run_id, stats, "normalization", "Normalizing fetches", 0.0)
        documents = await normalize_pending(
            store=store,
            source_run_id=fetch_run_id,
            progress=lambda done, total: _item_progress(
                store, run_id, "normalization", "Normalizing fetches", done, total, 0.0, 20.0
            ),
        )
        stats["normalized_documents"] = len(documents)
        _write_progress(store, run_id, stats, "normalization", "Normalizing fetches", 20.0)

        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 20.0)
        extractor_runs = await extract_pending(
            store=store,
            progress=lambda done, total: _item_progress(
                store, run_id, "extraction", "Extracting claims", done, total, 20.0, 55.0
            ),
        )
        stats["extractor_runs"] = [str(value) for value in extractor_runs]
        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 55.0)

        _write_progress(store, run_id, stats, "resolution", "Resolving mentions", 55.0)
        touched.update(
            await resolve_pending(
                store=store,
                progress=lambda done, total: _item_progress(
                    store, run_id, "resolution", "Resolving mentions", done, total, 55.0, 75.0
                ),
            )
        )
        stats["resolved_entities"] = len(touched)
        _write_progress(store, run_id, stats, "resolution", "Resolving mentions", 75.0)

        _write_progress(store, run_id, stats, "corroboration", "Promoting claims", 75.0)
        touched_claims = await corroborate_pending(store=store)
        stats["touched_claims"] = len(touched_claims)
        _write_progress(store, run_id, stats, "corroboration", "Promoting claims", 90.0)

        _write_progress(store, run_id, stats, "projection", "Rebuilding projections", 90.0)
        rebuilt = await rebuild_projections(touched or None, store=store)
        stats["projected_entities"] = len(rebuilt)
        store.update_source_run(
            run_id,
            stats=progress_stats(
                stats,
                stage="complete",
                status="complete",
                message="Pipeline complete",
                percent=100.0,
            ),
        )
        return rebuilt
    except Exception as exc:
        stats["pipeline_error"] = f"{type(exc).__name__}: {exc}"
        store.update_source_run(
            run_id,
            stats=progress_stats(
                stats,
                stage="failed",
                status="failed",
                message=stats["pipeline_error"],
                percent=100.0,
            ),
            error_message=stats["pipeline_error"],
        )
        raise


async def _item_progress(
    store: Store,
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
    run = store.get_source_run(run_id)
    stats = dict(run.stats or {}) if run else {}
    _write_progress(store, run_id, stats, stage, message, percent)


def _write_progress(
    store: Store,
    run_id: uuid.UUID,
    stats: dict[str, object],
    stage: str,
    message: str,
    percent: float,
) -> None:
    store.update_source_run(
        run_id,
        stats=progress_stats(
            stats,
            stage=stage,
            status="running",
            message=message,
            percent=percent,
        ),
    )
