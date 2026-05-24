from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import exists, select

from backend.db.models import Chunk, ClaimRaw, ExtractorRun
from backend.db.store import Store, utc_now
from backend.extraction.cascading_extractor import PROMPT_VERSION, extract_claims


async def extract_pending(
    workspace_id: str = "tuck",
    limit: int | None = None,
    *,
    store: Store,
) -> list[uuid.UUID]:
    del workspace_id
    with store.session() as session:
        query = (
            select(Chunk.id, Chunk.text)
            .where(~exists().where(ClaimRaw.chunk_id == Chunk.id))
            .order_by(Chunk.created_at.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        chunks = list(session.execute(query).all())

    run_ids: list[uuid.UUID] = []
    if not chunks:
        return run_ids

    extractor_run = _create_run(store)
    claims_emitted = 0
    chunks_processed = 0
    total_cost = 0.0
    model_names: set[str] = set()

    try:
        for chunk_id, text in chunks:
            result = await extract_claims(text)
            chunks_processed += 1
            total_cost += result.cost_usd
            model_names.add(result.model)
            with store.session() as session:
                for claim in result.claims:
                    session.add(
                        ClaimRaw(
                            chunk_id=chunk_id,
                            extractor_run_id=extractor_run.id,
                            subject_text=claim.subject_text,
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
        _finish_run(
            store,
            extractor_run.id,
            status="complete",
            chunks_processed=chunks_processed,
            claims_emitted=claims_emitted,
            cost_usd=total_cost,
            model=", ".join(sorted(model_names)) or extractor_run.model,
        )
    except Exception:
        _finish_run(
            store,
            extractor_run.id,
            status="failed",
            chunks_processed=chunks_processed,
            claims_emitted=claims_emitted,
            cost_usd=total_cost,
        )
        raise

    run_ids.append(extractor_run.id)
    return run_ids


def _create_run(store: Store) -> ExtractorRun:
    with store.session() as session:
        row = ExtractorRun(model="cascade", prompt_version=PROMPT_VERSION, status="running")
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
