from __future__ import annotations

import uuid
from datetime import datetime

from backend.corroboration.runner import corroborate_pending
from backend.db.store import Store
from backend.extraction.runner import extract_pending
from backend.normalization.runner import normalize_pending
from backend.progress import progress_stats
from backend.projections.runner import rebuild_projections
from backend.resolution.runner import resolve_pending

ACTIVE_RUN_STATUSES = {"queued", "running"}


class _RunStopped(Exception):
    pass


async def run_full_parse(
    source_id: uuid.UUID | str,
    *,
    store: Store,
    progress_run_id: uuid.UUID | str | None = None,
    scope: str = "unparsed",
    fetch_ids: list[uuid.UUID | str] | None = None,
    snapshot_at: datetime | str | None = None,
) -> set[uuid.UUID]:
    source_uuid = uuid.UUID(str(source_id))
    run_id = uuid.UUID(str(progress_run_id or source_id))
    fetch_uuid_list = [uuid.UUID(str(fetch_id)) for fetch_id in (fetch_ids or [])]
    snapshot = _parse_snapshot(snapshot_at)
    touched: set[uuid.UUID] = set()
    run = store.get_source_run(run_id)
    if run is None or run.status not in ACTIVE_RUN_STATUSES:
        return touched
    stats = dict(run.stats or {}) if run else {}
    try:
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "normalization", "Normalizing fetches", 0.0)
        normalize_kwargs: dict[str, object] = {"store": store, "source_id": source_uuid}
        if snapshot is not None:
            normalize_kwargs["snapshot_at"] = snapshot
        if scope == "all":
            normalize_kwargs.update({"pending_only": False})
        elif scope == "fetch_ids":
            normalize_kwargs.update(
                {
                    "fetch_ids": fetch_uuid_list,
                    "pending_only": False,
                }
            )
        documents = await normalize_pending(
            **normalize_kwargs,
            progress=lambda done, total: _item_progress(
                store, run_id, "normalization", "Normalizing fetches", done, total, 0.0, 20.0
            ),
        )
        stats["normalized_documents"] = len(documents)
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "normalization", "Normalizing fetches", 20.0)

        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 20.0)
        extractor_runs = await extract_pending(
            store=store,
            document_ids=documents,
            progress=lambda done, total: _item_progress(
                store, run_id, "extraction", "Extracting claims", done, total, 20.0, 55.0
            ),
        )
        stats["extractor_runs"] = [str(value) for value in extractor_runs]
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 55.0)

        _ensure_run_active(store, run_id)
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
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "resolution", "Resolving mentions", 75.0)

        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "corroboration", "Promoting claims", 75.0)
        touched_claims = await corroborate_pending(store=store)
        stats["touched_claims"] = len(touched_claims)
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "corroboration", "Promoting claims", 90.0)

        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "projection", "Rebuilding projections", 90.0)
        rebuilt = await rebuild_projections(touched or None, store=store)
        stats["projected_entities"] = len(rebuilt)
        _ensure_run_active(store, run_id)
        store.update_source_run(
            run_id,
            stats=progress_stats(
                stats,
                stage="complete",
                status="complete",
                message="Parse complete",
                percent=100.0,
            ),
        )
        return rebuilt
    except _RunStopped:
        return touched
    except Exception as exc:
        stats["parse_error"] = f"{type(exc).__name__}: {exc}"
        store.update_source_run(
            run_id,
            stats=progress_stats(
                stats,
                stage="failed",
                status="failed",
                message=stats["parse_error"],
                percent=100.0,
            ),
            error_message=stats["parse_error"],
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
    if run is None or run.status not in ACTIVE_RUN_STATUSES:
        raise _RunStopped
    stats = dict(run.stats or {}) if run else {}
    _write_progress(
        store,
        run_id,
        stats,
        stage,
        message,
        percent,
        data={"item_done": done, "item_total": total},
    )


def _write_progress(
    store: Store,
    run_id: uuid.UUID,
    stats: dict[str, object],
    stage: str,
    message: str,
    percent: float,
    data: dict[str, object] | None = None,
) -> None:
    _ensure_run_active(store, run_id)
    store.update_source_run(
        run_id,
        stats=progress_stats(
            stats,
            stage=stage,
            status="running",
            message=message,
            percent=percent,
            data=data,
        ),
    )


def _ensure_run_active(store: Store, run_id: uuid.UUID) -> None:
    run = store.get_source_run(run_id)
    if run is None or run.status not in ACTIVE_RUN_STATUSES:
        raise _RunStopped


def _parse_snapshot(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
