from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.db.models import Claim, ClaimConflict
from backend.db.store import Store

SINGLE_VALUED_PREDICATES = {
    "employed_by",
    "located_in",
    "current_employer",
    "current_role",
    "location",
}


def detect_conflicts(
    store: Store,
    touched_claim_ids: set[uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    del touched_claim_ids
    touched: set[uuid.UUID] = set()
    with store.session() as session:
        rows = list(
            session.execute(
                select(Claim.subject_entity_id, Claim.predicate)
                .where(Claim.status == "active")
                .where(Claim.predicate.in_(SINGLE_VALUED_PREDICATES))
                .distinct()
            ).all()
        )
        for subject_entity_id, predicate in rows:
            claims = list(
                session.execute(
                    select(Claim)
                    .where(Claim.subject_entity_id == subject_entity_id)
                    .where(Claim.predicate == predicate)
                    .where(Claim.status == "active")
                ).scalars()
            )
            for index, left in enumerate(claims):
                for right in claims[index + 1 :]:
                    if not _different_object(left, right):
                        continue
                    claim_a_id, claim_b_id = sorted([left.id, right.id])
                    existing = session.execute(
                        select(ClaimConflict).where(
                            ClaimConflict.claim_a_id == claim_a_id,
                            ClaimConflict.claim_b_id == claim_b_id,
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        continue
                    session.add(
                        ClaimConflict(
                            claim_a_id=claim_a_id,
                            claim_b_id=claim_b_id,
                            resolution="unresolved",
                        )
                    )
                    touched.update({claim_a_id, claim_b_id})
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    return touched


def _different_object(left: Claim, right: Claim) -> bool:
    return (left.object_entity_id, left.object_value) != (
        right.object_entity_id,
        right.object_value,
    )
