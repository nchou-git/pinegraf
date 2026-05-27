from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from backend.db.models import (
    Chunk,
    Claim,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Entity,
    EntityAlias,
    Fetch,
)
from backend.db.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose entity claim/evidence coverage in the live Pinegraf DB."
    )
    parser.add_argument("entity_name", help="Entity name or alias to match with pg_trgm")
    parser.add_argument("--threshold", type=float, default=0.6)
    args = parser.parse_args()

    store = Store()
    with store.session() as session:
        matches = _matching_entities(session, args.entity_name, args.threshold)
        payload = {
            "query": args.entity_name,
            "threshold": args.threshold,
            "matches": [
                _entity_payload(session, entity, score, matched_on)
                for entity, score, matched_on in matches
            ],
        }
    print(json.dumps(payload, indent=2, default=str))


def _matching_entities(
    session, entity_name: str, threshold: float
) -> list[tuple[Entity, float, str]]:
    canonical_rows = session.execute(
        select(
            Entity,
            func.similarity(Entity.canonical_name, entity_name).label("score"),
            Entity.canonical_name.label("matched_on"),
        )
        .where(func.similarity(Entity.canonical_name, entity_name) > threshold)
        .order_by(func.similarity(Entity.canonical_name, entity_name).desc())
    ).all()
    alias_rows = session.execute(
        select(
            Entity,
            func.similarity(EntityAlias.alias, entity_name).label("score"),
            EntityAlias.alias.label("matched_on"),
        )
        .join(EntityAlias, EntityAlias.entity_id == Entity.id)
        .where(func.similarity(EntityAlias.alias, entity_name) > threshold)
        .order_by(func.similarity(EntityAlias.alias, entity_name).desc())
    ).all()
    best: dict[uuid.UUID, tuple[Entity, float, str]] = {}
    for entity, score, matched_on in [*canonical_rows, *alias_rows]:
        current = best.get(entity.id)
        numeric_score = float(score or 0)
        if current is None or numeric_score > current[1]:
            best[entity.id] = (entity, numeric_score, str(matched_on))
    return sorted(best.values(), key=lambda item: item[1], reverse=True)


def _entity_payload(session, entity: Entity, score: float, matched_on: str) -> dict[str, Any]:
    claims = _claims_for_entity(session, entity.id)
    evidence_docs: set[uuid.UUID] = set()
    evidence_urls: set[str] = set()
    claim_payloads = []
    for claim, direction, subject, obj in claims:
        evidence = _claim_evidence(session, claim.id)
        for row in evidence:
            if row["document_id"]:
                evidence_docs.add(uuid.UUID(row["document_id"]))
            if row["url"]:
                evidence_urls.add(row["url"])
        claim_payloads.append(
            {
                "id": str(claim.id),
                "direction": direction,
                "subject": _entity_name(subject),
                "predicate": claim.predicate,
                "object": _entity_name(obj) if obj is not None else claim.object_value,
                "confidence": claim.confidence
                if claim.confidence is not None
                else claim.confidence_score,
                "evidence_urls": evidence,
            }
        )
    return {
        "entity": {
            "id": str(entity.id),
            "canonical_name": entity.canonical_name,
            "kind": entity.kind,
            "matched_on": matched_on,
            "similarity": round(score, 4),
        },
        "claims": claim_payloads,
        "coverage_summary": {
            "distinct_documents": len(evidence_docs),
            "distinct_source_urls": len(evidence_urls),
        },
    }


def _claims_for_entity(
    session, entity_id: uuid.UUID
) -> list[tuple[Claim, str, Entity, Entity | None]]:
    subject_entity = aliased(Entity)
    object_entity = aliased(Entity)
    rows = session.execute(
        select(Claim, subject_entity, object_entity)
        .join(subject_entity, subject_entity.id == Claim.subject_entity_id)
        .outerjoin(object_entity, object_entity.id == Claim.object_entity_id)
        .where(or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id))
        .order_by(Claim.first_seen_at.desc())
    ).all()
    claims = []
    for claim, subject, obj in rows:
        direction = "outbound" if claim.subject_entity_id == entity_id else "inbound"
        claims.append((claim, direction, subject, obj))
    return claims


def _claim_evidence(session, claim_id: uuid.UUID) -> list[dict[str, str | None]]:
    rows = session.execute(
        select(
            Document.id,
            Document.canonical_url,
            Fetch.url,
            func.max(Fetch.fetched_at),
        )
        .select_from(ClaimEvidence)
        .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
        .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
        .join(Document, Document.id == Chunk.document_id)
        .outerjoin(DocumentFetch, DocumentFetch.document_id == Document.id)
        .outerjoin(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .where(ClaimEvidence.claim_id == claim_id)
        .group_by(Document.id, Document.canonical_url, Fetch.url)
        .order_by(func.max(Fetch.fetched_at).desc().nullslast())
    ).all()
    return [
        {
            "document_id": str(document_id),
            "canonical_url": canonical_url,
            "url": canonical_url or url,
            "latest_fetched_at": fetched_at.isoformat() if fetched_at else None,
        }
        for document_id, canonical_url, url, fetched_at in rows
    ]


def _entity_name(entity: Entity | None) -> str | None:
    return entity.canonical_name if entity is not None else None


if __name__ == "__main__":
    main()
