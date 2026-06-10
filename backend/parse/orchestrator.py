from __future__ import annotations

import uuid
from datetime import datetime

from backend.db.store import Store, utc_now
from backend.extraction.runner import extract_pending
from backend.normalization.runner import normalize_pending
from backend.progress import progress_stats

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
    snapshot = _parse_snapshot(snapshot_at) or utc_now()
    run = store.get_source_run(run_id)
    if run is None or run.status not in ACTIVE_RUN_STATUSES:
        return set()
    stats = dict(run.stats or {}) if run else {}
    frozen_fetch_ids = _parse_fetch_ids(
        store,
        source_uuid,
        scope=scope,
        fetch_ids=fetch_uuid_list,
        snapshot_at=snapshot,
    )
    total_to_parse = len(frozen_fetch_ids)
    stats["total_to_parse"] = total_to_parse
    stats["items_parsed"] = int(stats.get("items_parsed") or 0)
    try:
        _ensure_run_active(store, run_id)
        _write_progress(
            store,
            run_id,
            stats,
            "normalization",
            "Normalizing fetches",
            0.0 if total_to_parse else 100.0,
            data={"total_to_parse": total_to_parse, "items_parsed": 0},
        )
        normalize_kwargs: dict[str, object] = {"store": store, "source_id": source_uuid}
        if snapshot is not None:
            normalize_kwargs["snapshot_at"] = snapshot
        normalize_kwargs.update({"fetch_ids": frozen_fetch_ids, "pending_only": False})
        documents = await normalize_pending(
            **normalize_kwargs,
            progress=lambda done, total: _item_progress(
                store,
                run_id,
                "normalization",
                "Normalizing fetches",
                done,
                total,
                0.0,
                20.0,
                total_to_parse=total_to_parse,
                items_parsed=done,
            ),
        )
        stats["items_parsed"] = len(documents)
        stats["normalized_documents"] = len(documents)
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "normalization", "Normalizing fetches", 20.0)

        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 20.0)
        extractor_runs = await extract_pending(
            store=store,
            document_ids=documents,
            progress=lambda done, total: _item_progress(
                store, run_id, "extraction", "Extracting claims", done, total, 20.0, 100.0
            ),
        )
        stats["extractor_runs"] = [str(value) for value in extractor_runs]
        _ensure_run_active(store, run_id)
        _write_progress(store, run_id, stats, "extraction", "Extracting claims", 100.0)

        store.update_source_run(
            run_id,
            status="complete",
            stats=progress_stats(
                stats,
                stage="complete",
                status="complete",
                message="Parse complete",
                percent=100.0,
                data={"items_parsed": len(documents), "total_to_parse": total_to_parse},
            ),
            finished=True,
        )
        return set()
    except _RunStopped:
        return set()
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
    total_to_parse: int | None = None,
    items_parsed: int | None = None,
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
        data={
            "item_done": done,
            "item_total": total,
            **({"total_to_parse": total_to_parse} if total_to_parse is not None else {}),
            **({"items_parsed": items_parsed} if items_parsed is not None else {}),
        },
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


def _parse_fetch_ids(
    store: Store,
    source_id: uuid.UUID,
    *,
    scope: str,
    fetch_ids: list[uuid.UUID],
    snapshot_at: datetime,
) -> list[uuid.UUID]:
    if scope == "all":
        return store.fetch_ids_for_source(source_id, snapshot_at=snapshot_at)
    if scope == "fetch_ids":
        return store.fetch_ids_for_source(
            source_id,
            fetch_ids=fetch_ids,
            snapshot_at=snapshot_at,
        )
    return store.pending_fetch_ids(source_id=source_id, snapshot_at=snapshot_at)
