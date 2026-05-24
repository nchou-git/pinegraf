from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from backend.db.models import Claim, ClaimEvidence, HumanSignal
from backend.db.store import Store, utc_now


def rescore_claim(store: Store, claim_id: uuid.UUID) -> float:
    now = utc_now()
    with store.session() as session:
        claim = session.get(Claim, claim_id)
        if claim is None:
            return 0.0
        evidences = list(
            session.execute(
                select(ClaimEvidence).where(ClaimEvidence.claim_id == claim_id)
            ).scalars()
        )
        if not evidences:
            claim.confidence_score = 0.0
            session.commit()
            return 0.0
        product = 1.0
        for evidence in evidences:
            days = max((now - _aware_utc(evidence.added_at)).days, 0)
            recency_factor = math.exp(-days / 365)
            product *= 1 - max(min(evidence.weight * recency_factor, 1), 0)
        score = min(max(1 - product, 0.05), 0.99)
        signals = list(
            session.execute(
                select(HumanSignal.signal_type).where(
                    HumanSignal.target_type == "claim",
                    HumanSignal.target_id == claim_id,
                )
            ).scalars()
        )
        if "verify" in signals:
            score = max(score, 0.95)
        if "dispute" in signals:
            score = min(score, 0.50)
        claim.confidence_score = score
        session.commit()
        return score


def rescore_claims(store: Store, claim_ids: set[uuid.UUID]) -> None:
    for claim_id in claim_ids:
        rescore_claim(store, claim_id)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
