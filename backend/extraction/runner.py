from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import exists, select

from backend.class_year import normalize_class_year
from backend.config import get_settings
from backend.db.models import AuditLog, ClaimRaw, Document, ExtractorRun
from backend.db.store import Store, utc_now
from backend.extraction.extractor import PROMPT_VERSION, ExtractedClaim, extract_claims


async def extract_pending(
    limit: int | None = None,
    *,
    store: Store,
    document_ids: list[uuid.UUID] | None = None,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[uuid.UUID]:
    with store.session() as session:
        query = (
            select(Document.id, Document.cleaned_text)
            .where(~exists().where(ClaimRaw.document_id == Document.id))
            .order_by(Document.created_at.asc())
        )
        if document_ids is not None:
            if not document_ids:
                return []
            query = query.where(Document.id.in_(document_ids))
        if limit is not None:
            query = query.limit(limit)
        documents = list(session.execute(query).all())

    run_ids: list[uuid.UUID] = []
    if not documents:
        return run_ids

    extractor_run = _create_run(store)
    claims_emitted = 0
    documents_processed = 0
    total_cost = 0.0
    model_names: set[str] = set()

    try:
        total = len(documents)
        for document_id, text in documents:
            # TODO: handle documents that exceed the extraction model context window.
            result = await extract_claims(text)
            documents_processed += 1
            total_cost += result.cost_usd
            model_names.add(result.model)
            with store.session() as session:
                if result.rejected_claims:
                    session.add(
                        AuditLog(
                            action="extraction.rejected",
                            target_table="documents",
                            target_id=str(document_id),
                            actor="system",
                            payload={"rejected": result.rejected_claims},
                        )
                    )
                for claim in result.claims:
                    claim = normalize_extracted_claim(claim)
                    session.add(
                        ClaimRaw(
                            document_id=document_id,
                            extractor_run_id=extractor_run.id,
                            subject_text=claim.subject_text,
                            subject_type=claim.subject_type,
                            predicate=claim.predicate,
                            object_text=claim.object_text,
                            object_type=claim.object_type,
                            qualifiers=claim.qualifiers,
                            confidence_internal=claim.confidence_internal,
                            raw_quote=claim.raw_quote,
                            span_start=claim.span_start,
                            span_end=claim.span_end,
                        )
                    )
                    claims_emitted += 1
                session.commit()
            if progress is not None:
                await progress(documents_processed, total)
        _finish_run(
            store,
            extractor_run.id,
            status="complete",
            chunks_processed=documents_processed,
            claims_emitted=claims_emitted,
            cost_usd=total_cost,
            model=", ".join(sorted(model_names)) or extractor_run.model,
        )
    except Exception:
        _finish_run(
            store,
            extractor_run.id,
            status="failed",
            chunks_processed=documents_processed,
            claims_emitted=claims_emitted,
            cost_usd=total_cost,
        )
        raise

    run_ids.append(extractor_run.id)
    return run_ids


def normalize_extracted_claim(claim: ExtractedClaim) -> ExtractedClaim:
    if claim.predicate != "class_year":
        return claim
    year = normalize_class_year(claim.object_text) or normalize_class_year(claim.raw_quote)
    if year is None:
        return claim
    return claim.model_copy(
        update={
            "object_text": str(year),
            "object_type": "attribute_value",
        }
    )


def _create_run(store: Store) -> ExtractorRun:
    with store.session() as session:
        row = ExtractorRun(
            model=get_settings().extraction_model,
            prompt_version=PROMPT_VERSION,
            status="running",
        )
        session.add(row)
        session.commit()
        return row


def _finish_run(
    store: Store,
    run_id: uuid.UUID,
    *,
    status: str,
    chunks_processed: int,
    claims_emitted: int,
    cost_usd: float,
    model: str | None = None,
) -> None:
    with store.session() as session:
        row = session.get(ExtractorRun, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = utc_now()
        row.chunks_processed = chunks_processed
        row.claims_emitted = claims_emitted
        row.cost_usd = Decimal(str(round(cost_usd, 4)))
        if model is not None:
            row.model = model
        session.commit()
