from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, select
from sqlalchemy.exc import IntegrityError

from backend.db.models import (
    Chunk,
    Claim,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    EntityMention,
    Fetch,
    Source,
    SourceRun,
)
from backend.db.store import Store, utc_now


def promote_pending(store: Store, *, valid_from: datetime | None = None) -> set[uuid.UUID]:
    touched: set[uuid.UUID] = set()
    with store.session() as session:
        rows = list(
            session.execute(
                select(ClaimRaw)
                .where(~exists().where(ClaimEvidence.claim_raw_id == ClaimRaw.id))
                .order_by(ClaimRaw.extracted_at.asc())
            ).scalars()
        )

    for raw in rows:
        claim_id = promote_claim_raw(store, raw.id, valid_from=valid_from)
        if claim_id is not None:
            touched.add(claim_id)
    return touched


def promote_claim_raw(
    store: Store,
    claim_raw_id: uuid.UUID,
    *,
    valid_from: datetime | None = None,
) -> uuid.UUID | None:
    with store.session() as session:
        raw = session.get(ClaimRaw, claim_raw_id)
        if raw is None:
            return None
        mentions = {
            mention.position: mention
            for mention in session.execute(
                select(EntityMention).where(EntityMention.claim_raw_id == claim_raw_id)
            ).scalars()
        }
        subject = mentions.get("subject")
        if subject is None:
            return None
        object_mention = mentions.get("object")
        object_entity_id = object_mention.entity_id if object_mention is not None else None
        object_value = None if object_entity_id is not None else raw.object_text
        if object_entity_id is None and not object_value:
            return None

        claim = _find_claim(
            session,
            subject_entity_id=subject.entity_id,
            predicate=raw.predicate,
            object_entity_id=object_entity_id,
            object_value=object_value,
        )
        if claim is None:
            claim = Claim(
                subject_entity_id=subject.entity_id,
                predicate=raw.predicate,
                object_entity_id=object_entity_id,
                object_value=object_value,
                qualifiers=raw.qualifiers,
                valid_from=valid_from or utc_now(),
                last_corroborated_at=utc_now(),
            )
            session.add(claim)
            session.flush()
        else:
            claim.last_corroborated_at = utc_now()

        source = _source_for_raw(session, raw.id)
        if source is None:
            return None
        resolution_confidence = min(
            [mention.resolution_confidence for mention in mentions.values()] or [1.0]
        )
        weight = (
            source.trust_weight
            * resolution_confidence
            * (raw.confidence_internal if raw.confidence_internal is not None else 0.5)
        )
        session.add(
            ClaimEvidence(
                claim_id=claim.id,
                claim_raw_id=raw.id,
                source_id=source.id,
                weight=max(min(weight, 1.0), 0.0),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        return claim.id


def _find_claim(
    session,
    *,
    subject_entity_id: uuid.UUID,
    predicate: str,
    object_entity_id: uuid.UUID | None,
    object_value: str | None,
) -> Claim | None:
    conditions = [
        Claim.subject_entity_id == subject_entity_id,
        Claim.predicate == predicate,
    ]
    if object_entity_id is not None:
        conditions.append(Claim.object_entity_id == object_entity_id)
    else:
        conditions.extend([Claim.object_entity_id.is_(None), Claim.object_value == object_value])
    return session.execute(select(Claim).where(and_(*conditions))).scalar_one_or_none()


def _source_for_raw(session, claim_raw_id: uuid.UUID) -> Source | None:
    return session.execute(
        select(Source)
        .join(SourceRun, SourceRun.source_id == Source.id)
        .join(Fetch, Fetch.source_run_id == SourceRun.id)
        .join(DocumentFetch, DocumentFetch.fetch_id == Fetch.id)
        .join(Document, Document.id == DocumentFetch.document_id)
        .join(Chunk, Chunk.document_id == Document.id)
        .join(ClaimRaw, ClaimRaw.chunk_id == Chunk.id)
        .where(ClaimRaw.id == claim_raw_id)
        .limit(1)
    ).scalar_one_or_none()
