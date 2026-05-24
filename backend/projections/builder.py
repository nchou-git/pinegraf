from __future__ import annotations

import statistics
import uuid
from collections import defaultdict

from sqlalchemy import delete, or_, select

from backend.db.models import (
    Claim,
    ClaimEvidence,
    Entity,
    EntityNeighborhood,
    EntitySummary,
)
from backend.db.store import Store

PRIMARY_ATTRIBUTE_PREDICATES = {
    "current_employer",
    "current_title",
    "location",
    "class_year",
    "degrees",
    "employed_by",
    "located_in",
    "studied_at",
}


def rebuild_entity_projections(
    store: Store,
    entity_ids: set[uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    with store.session() as session:
        if entity_ids is None:
            ids = set(session.execute(select(Entity.id)).scalars())
        else:
            ids = set(entity_ids)
            neighbor_rows = session.execute(
                select(Claim.subject_entity_id, Claim.object_entity_id)
                .where(
                    or_(
                        Claim.subject_entity_id.in_(ids),
                        Claim.object_entity_id.in_(ids),
                    )
                )
                .where(Claim.object_entity_id.is_not(None))
            ).all()
            for subject_id, object_id in neighbor_rows:
                ids.add(subject_id)
                if object_id is not None:
                    ids.add(object_id)

        if not ids:
            return set()

        session.execute(delete(EntitySummary).where(EntitySummary.entity_id.in_(ids)))
        session.execute(
            delete(EntityNeighborhood).where(
                or_(
                    EntityNeighborhood.entity_id.in_(ids),
                    EntityNeighborhood.neighbor_id.in_(ids),
                )
            )
        )

        entities = {
            entity.id: entity
            for entity in session.execute(select(Entity).where(Entity.id.in_(ids))).scalars()
        }
        claims = list(
            session.execute(
                select(Claim).where(
                    or_(
                        Claim.subject_entity_id.in_(ids),
                        Claim.object_entity_id.in_(ids),
                    )
                )
            ).scalars()
        )
        evidence_counts: dict[uuid.UUID, int] = defaultdict(int)
        for claim_id in session.execute(
            select(ClaimEvidence.claim_id).where(
                ClaimEvidence.claim_id.in_([claim.id for claim in claims] or [uuid.uuid4()])
            )
        ).scalars():
            evidence_counts[claim_id] += 1
        source_counts = _source_counts(session, claims)

        for entity_id, entity in entities.items():
            related = [
                claim
                for claim in claims
                if claim.subject_entity_id == entity_id or claim.object_entity_id == entity_id
            ]
            neighbors = {
                claim.object_entity_id
                for claim in related
                if claim.subject_entity_id == entity_id and claim.object_entity_id is not None
            } | {
                claim.subject_entity_id for claim in related if claim.object_entity_id == entity_id
            }
            confidence_values = [claim.confidence_score for claim in related]
            session.add(
                EntitySummary(
                    entity_id=entity_id,
                    display_name=entity.canonical_name,
                    primary_attributes=_primary_attributes(related),
                    connection_count=len(neighbors),
                    source_count=source_counts.get(entity_id, 0),
                    confidence_avg=statistics.mean(confidence_values)
                    if confidence_values
                    else None,
                )
            )

        neighborhood = _neighborhood_rows(claims, evidence_counts)
        for (entity_id, neighbor_id), data in neighborhood.items():
            if entity_id in ids:
                session.add(
                    EntityNeighborhood(
                        entity_id=entity_id,
                        neighbor_id=neighbor_id,
                        predicates=sorted(data["predicates"]),
                        evidence_count=int(data["evidence_count"]),
                        confidence=float(data["confidence"]),
                    )
                )
        session.commit()
        return ids


def _primary_attributes(claims: list[Claim]) -> dict[str, object]:
    output: dict[str, object] = {}
    for claim in sorted(claims, key=lambda row: row.confidence_score, reverse=True):
        if claim.predicate not in PRIMARY_ATTRIBUTE_PREDICATES:
            continue
        if claim.predicate in output:
            continue
        if claim.object_value:
            output[claim.predicate] = claim.object_value
        elif claim.object_entity_id:
            output[claim.predicate] = str(claim.object_entity_id)
    return output


def _source_counts(session, claims: list[Claim]) -> dict[uuid.UUID, int]:
    source_ids: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if not claims:
        return {}
    evidence = list(
        session.execute(
            select(ClaimEvidence.claim_id, ClaimEvidence.source_id).where(
                ClaimEvidence.claim_id.in_([claim.id for claim in claims])
            )
        ).all()
    )
    claim_by_id = {claim.id: claim for claim in claims}
    for claim_id, source_id in evidence:
        claim = claim_by_id[claim_id]
        source_ids[claim.subject_entity_id].add(source_id)
        if claim.object_entity_id is not None:
            source_ids[claim.object_entity_id].add(source_id)
    return {entity_id: len(values) for entity_id, values in source_ids.items()}


def _neighborhood_rows(claims: list[Claim], evidence_per_claim: dict[uuid.UUID, int]):
    rows: dict[tuple[uuid.UUID, uuid.UUID], dict[str, object]] = {}
    for claim in claims:
        if claim.object_entity_id is None:
            continue
        for entity_id, neighbor_id in (
            (claim.subject_entity_id, claim.object_entity_id),
            (claim.object_entity_id, claim.subject_entity_id),
        ):
            key = (entity_id, neighbor_id)
            rows.setdefault(key, {"predicates": set(), "evidence_count": 0, "confidence": 0.0})
            rows[key]["predicates"].add(claim.predicate)
            rows[key]["evidence_count"] += evidence_per_claim.get(claim.id, 0)
            rows[key]["confidence"] = max(float(rows[key]["confidence"]), claim.confidence_score)
    return rows
