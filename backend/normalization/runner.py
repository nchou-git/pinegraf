from __future__ import annotations

import uuid

from backend.db.store import Store
from backend.normalization.normalizer import normalize_fetch


async def normalize_pending(
    *,
    store: Store,
    source_run_id: uuid.UUID | str | None = None,
) -> list[uuid.UUID]:
    run_uuid = uuid.UUID(str(source_run_id)) if source_run_id is not None else None
    document_ids: list[uuid.UUID] = []
    for fetch_id in store.pending_fetch_ids(source_run_id=run_uuid):
        document_ids.append(await normalize_fetch(fetch_id, store=store))
    return document_ids


async def normalize_run(source_run_id: uuid.UUID | str, *, store: Store) -> list[uuid.UUID]:
    return await normalize_pending(store=store, source_run_id=source_run_id)
