from __future__ import annotations

import uuid
from datetime import datetime

from backend.corroboration.confidence_scorer import rescore_claims
from backend.corroboration.conflict_detector import detect_conflicts
from backend.corroboration.promoter import promote_pending
from backend.db.store import Store


async def corroborate_pending(
    *,
    store: Store,
    valid_from: datetime | None = None,
) -> set[uuid.UUID]:
    touched = promote_pending(store, valid_from=valid_from)
    touched.update(detect_conflicts(store))
    rescore_claims(store, touched)
    return touched
