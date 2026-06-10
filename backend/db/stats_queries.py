from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import (
    Claim,
    ClaimConflict,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Entity,
    EntityMention,
    Fetch,
    Source,
    SourceRun,
)

HTTP_SUCCESS = (200, 299)
PENDING_PARSE_CAP = 10000


def pages_fetched(session: Session, source_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(Fetch.url)))
            .select_from(Fetch)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
            .where(Fetch.http_status.between(*HTTP_SUCCESS))
            .where((Fetch.body_bytes.is_not(None)) | (Fetch.body_unchanged_since.is_not(None)))
        ).scalar_one()
    )


def urls_known(session: Session, source_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(Fetch.url)))
            .select_from(Fetch)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        ).scalar_one()
    )


def documents_for_source(session: Session, source_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(Document.id)))
            .select_from(Document)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        ).scalar_one()
    )


def claims_for_source(session: Session, source_id: uuid.UUID) -> int:
    # Claim provenance runs through evidence -> raw extraction -> document
    # -> document_fetch -> fetch -> source_run. This counts claims actually supported
    # by documents linked to the source, not unrelated projected graph rows.
    return int(
        session.execute(
            select(func.count(func.distinct(Claim.id)))
            .select_from(Claim)
            .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
            .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
            .join(Document, Document.id == ClaimRaw.document_id)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        ).scalar_one()
    )


def entities_for_source(session: Session, source_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count(func.distinct(Entity.id)))
            .select_from(Entity)
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .join(ClaimRaw, ClaimRaw.id == EntityMention.claim_raw_id)
            .join(Document, Document.id == ClaimRaw.document_id)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        ).scalar_one()
    )


def pending_parse_count(
    session: Session,
    source_id: uuid.UUID,
    *,
    snapshot_at: datetime | None = None,
) -> int:
    query = _pending_fetch_query(source_id=source_id, snapshot_at=snapshot_at).limit(
        PENDING_PARSE_CAP
    )
    return len(session.execute(query).scalars().all())


def pending_fetch_ids(
    session: Session,
    *,
    source_id: uuid.UUID | None = None,
    fetch_ids: list[uuid.UUID] | tuple[uuid.UUID, ...] | None = None,
    snapshot_at: datetime | None = None,
) -> list[uuid.UUID]:
    query = _pending_fetch_query(source_id=source_id, snapshot_at=snapshot_at)
    if fetch_ids is not None:
        query = query.where(Fetch.id.in_(fetch_ids))
    return list(session.execute(query).scalars())


def source_coverage(session: Session, source_id: uuid.UUID) -> dict[str, int]:
    fetched = pages_fetched(session, source_id)
    known = urls_known(session, source_id)
    documents = documents_for_source(session, source_id)
    claims = claims_for_source(session, source_id)
    entities = entities_for_source(session, source_id)
    pending = pending_parse_count(session, source_id)
    conflicts = conflicts_for_source(session, source_id)
    return {
        "pages_fetched": fetched,
        "urls_known": max(known, fetched),
        "documents_parsed": documents,
        "documents": documents,
        "claims": claims,
        "entities": entities,
        "conflicts": conflicts,
        "pending_parse_count": pending,
    }


def source_coverage_many(
    session: Session,
    source_ids: list[uuid.UUID] | tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, dict[str, int]]:
    coverage = {source_id: _empty_source_coverage() for source_id in source_ids}
    if not source_ids:
        return coverage

    for source_id, count in session.execute(
        select(SourceRun.source_id, func.count(func.distinct(Fetch.url)))
        .select_from(Fetch)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .where(Fetch.http_status.between(*HTTP_SUCCESS))
        .where((Fetch.body_bytes.is_not(None)) | (Fetch.body_unchanged_since.is_not(None)))
        .group_by(SourceRun.source_id)
    ):
        coverage[source_id]["pages_fetched"] = int(count or 0)

    for source_id, count in session.execute(
        select(SourceRun.source_id, func.count(func.distinct(Fetch.url)))
        .select_from(Fetch)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .group_by(SourceRun.source_id)
    ):
        coverage[source_id]["urls_known"] = int(count or 0)

    for source_id, count in session.execute(
        select(SourceRun.source_id, func.count(func.distinct(Document.id)))
        .select_from(Document)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .group_by(SourceRun.source_id)
    ):
        documents = int(count or 0)
        coverage[source_id]["documents"] = documents
        coverage[source_id]["documents_parsed"] = documents

    for source_id, count in session.execute(
        select(SourceRun.source_id, func.count(func.distinct(Claim.id)))
        .select_from(Claim)
        .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
        .join(Document, Document.id == ClaimRaw.document_id)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .group_by(SourceRun.source_id)
    ):
        coverage[source_id]["claims"] = int(count or 0)

    for source_id, count in session.execute(
        select(SourceRun.source_id, func.count(func.distinct(Entity.id)))
        .select_from(Entity)
        .join(EntityMention, EntityMention.entity_id == Entity.id)
        .join(ClaimRaw, ClaimRaw.id == EntityMention.claim_raw_id)
        .join(Document, Document.id == ClaimRaw.document_id)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .group_by(SourceRun.source_id)
    ):
        coverage[source_id]["entities"] = int(count or 0)

    for source_id, count in session.execute(
        _pending_fetch_query(source_id=None, snapshot_at=None)
        .with_only_columns(SourceRun.source_id, func.count(Fetch.id))
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .group_by(SourceRun.source_id)
        .order_by(None)
    ):
        coverage[source_id]["pending_parse_count"] = min(int(count or 0), PENDING_PARSE_CAP)

    claim_sources = (
        select(SourceRun.source_id.label("source_id"), Claim.id.label("claim_id"))
        .select_from(Claim)
        .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
        .join(Document, Document.id == ClaimRaw.document_id)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id.in_(source_ids))
        .distinct()
        .subquery()
    )
    for source_id, count in session.execute(
        select(claim_sources.c.source_id, func.count(func.distinct(ClaimConflict.id)))
        .select_from(claim_sources)
        .join(
            ClaimConflict,
            (ClaimConflict.claim_a_id == claim_sources.c.claim_id)
            | (ClaimConflict.claim_b_id == claim_sources.c.claim_id),
        )
        .group_by(claim_sources.c.source_id)
    ):
        coverage[source_id]["conflicts"] = int(count or 0)

    for values in coverage.values():
        values["urls_known"] = max(values["urls_known"], values["pages_fetched"])
    return coverage


def _empty_source_coverage() -> dict[str, int]:
    return {
        "pages_fetched": 0,
        "urls_known": 0,
        "documents_parsed": 0,
        "documents": 0,
        "claims": 0,
        "entities": 0,
        "conflicts": 0,
        "pending_parse_count": 0,
    }


def conflicts_for_source(session: Session, source_id: uuid.UUID) -> int:
    claim_ids = (
        select(Claim.id)
        .select_from(Claim)
        .join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
        .join(Document, Document.id == ClaimRaw.document_id)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id == source_id)
    )
    return int(
        session.execute(
            select(func.count())
            .select_from(ClaimConflict)
            .where(
                (ClaimConflict.claim_a_id.in_(claim_ids))
                | (ClaimConflict.claim_b_id.in_(claim_ids))
            )
        ).scalar_one()
    )


def global_stats(session: Session) -> dict[str, int]:
    source_ids = list(
        session.execute(select(Source.id).where(Source.status != "archived")).scalars()
    )
    totals = {
        "documents": 0,
        "claims": 0,
        "entities": 0,
        "sources": len(source_ids),
        "pages_fetched": 0,
        "urls_known": 0,
        "pending_parse_count": 0,
    }
    for coverage in source_coverage_many(session, source_ids).values():
        totals["documents"] += coverage["documents"]
        totals["claims"] += coverage["claims"]
        totals["entities"] += coverage["entities"]
        totals["pages_fetched"] += coverage["pages_fetched"]
        totals["urls_known"] += coverage["urls_known"]
        totals["pending_parse_count"] += coverage["pending_parse_count"]
    return totals


def _pending_fetch_query(
    *,
    source_id: uuid.UUID | None,
    snapshot_at: datetime | None,
):
    query = (
        select(Fetch.id)
        .select_from(Fetch)
        .outerjoin(DocumentFetch, DocumentFetch.fetch_id == Fetch.id)
        .where(DocumentFetch.fetch_id.is_(None))
        .where(Fetch.http_status.between(*HTTP_SUCCESS))
        .where(Fetch.body_bytes.is_not(None))
        .where(Fetch.body_unchanged_since.is_(None))
        .where(Fetch.parse_skip_reason.is_(None))
        .order_by(Fetch.fetched_at.asc(), Fetch.id.asc())
    )
    if source_id is not None:
        query = query.join(SourceRun, SourceRun.id == Fetch.source_run_id).where(
            SourceRun.source_id == source_id
        )
    if snapshot_at is not None:
        query = query.where(Fetch.fetched_at <= snapshot_at)
    return query
