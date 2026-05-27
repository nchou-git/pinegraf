from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from backend.db.store import Store
from backend.normalization.normalizer import normalize_fetch


async def normalize_pending(
    *,
    store: Store,
    source_id: uuid.UUID | str | None = None,
    fetch_ids: list[uuid.UUID] | None = None,
    snapshot_at=None,
    pending_only: bool = True,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[uuid.UUID]:
    source_uuid = uuid.UUID(str(source_id)) if source_id is not None else None
    document_ids: list[uuid.UUID] = []
    pending_fetch_ids = (
        store.pending_fetch_ids(
            source_id=source_uuid,
            fetch_ids=fetch_ids,
            snapshot_at=snapshot_at,
        )
        if pending_only
        else store.fetch_ids_for_source(
            source_uuid,
            fetch_ids=fetch_ids,
            snapshot_at=snapshot_at,
        )
        if source_uuid is not None
        else list(fetch_ids or [])
    )
    total = len(pending_fetch_ids)
    for index, fetch_id in enumerate(pending_fetch_ids, start=1):
        document_ids.append(await normalize_fetch(fetch_id, store=store, valid_from=snapshot_at))
        if progress is not None:
            await progress(index, total)
    return document_ids
