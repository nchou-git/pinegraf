from __future__ import annotations

import uuid

from backend.db.store import Store
from backend.projections.builder import rebuild_entity_projections


async def rebuild_projections(
    entity_ids: set[uuid.UUID] | None = None,
    *,
    store: Store,
) -> set[uuid.UUID]:
    return rebuild_entity_projections(store, entity_ids)
