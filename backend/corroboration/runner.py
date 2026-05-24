from __future__ import annotations

import uuid

from backend.corroboration.confidence_scorer import rescore_claims
from backend.corroboration.conflict_detector import detect_conflicts
from backend.corroboration.promoter import promote_pending
from backend.db.store import Store


async def corroborate_pending(
    workspace_id: str = "tuck",
    *,
    store: Store,
) -> set[uuid.UUID]:
    del workspace_id
    touched = promote_pending(store)
    touched.update(detect_conflicts(store, touched))
    rescore_claims(store, touched)
    return touched
