from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from backend.db.store import Store
from backend.normalization.normalizer import normalize_fetch


async def normalize_pending(
    *,
    store: Store,
    source_run_id: uuid.UUID | str | None = None,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[uuid.UUID]:
    run_uuid = uuid.UUID(str(source_run_id)) if source_run_id is not None else None
    document_ids: list[uuid.UUID] = []
    fetch_ids = store.pending_fetch_ids(source_run_id=run_uuid)
    total = len(fetch_ids)
    for index, fetch_id in enumerate(fetch_ids, start=1):
        document_ids.append(await normalize_fetch(fetch_id, store=store))
        if progress is not None:
            await progress(index, total)
    return document_ids
