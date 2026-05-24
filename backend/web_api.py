from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator

from openai import AsyncOpenAI
from sqlalchemy import delete, func, or_, select

from backend.config import get_settings
from backend.corroboration.confidence_scorer import rescore_claim
from backend.db.models import (
    Chunk,
    Claim,
    ClaimConflict,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Entity,
    EntityAlias,
    EntityMention,
    EntityNeighborhood,
    EntitySummary,
    Fetch,
    HumanSignal,
    Source,
    SourceRun,
)
from backend.db.store import SCHEMA_TABLES, Store, utc_now
from backend.resolution.embedder import embed_text

ASK_CACHE_SECONDS = 3600
ASK_CACHE_MAX = 100
_ASK_CACHE: OrderedDict[str, tuple[float, str, list[dict[str, object]]]] = OrderedDict()


def stats(store: Store) -> dict[str, int]:
    return store.table_counts(SCHEMA_TABLES)


def list_directory(
    store: Store,
    *,
    q: str = "",
    org: str = "",
    class_year: str = "",
    source: str = "",
    min_confidence: float = 0.0,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with store.session() as session:
        rows = list(
            session.execute(
                select(EntitySummary, Entity)
                .join(Entity, Entity.id == EntitySummary.entity_id)
                .order_by(
                    EntitySummary.confidence_avg.desc().nullslast(),
                    Entity.canonical_name.asc(),
                )
            ).all()
        )
        filtered = []
        for summary, entity in rows:
            primary = summary.primary_attributes or {}
            haystack = " ".join(
                [
                    entity.canonical_name,
                    entity.kind,
                    json.dumps(primary, default=str),
                ]
            ).casefold()
            if q and q.casefold() not in haystack:
                continue
            if org and org.casefold() not in haystack:
                continue
            if class_year and class_year.casefold() not in haystack:
                continue
            if summary.confidence_avg is not None and summary.confidence_avg < min_confidence:
                continue
            source_mix = _source_mix(session, entity.id)
            if source and source.lower() not in {"all", ""} and source not in source_mix:
                continue
            filtered.append((summary, entity, source_mix))
        total = len(filtered)
        page_rows = filtered[(page - 1) * page_size : page * page_size]
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "results": [
                {
                    "entity_id": str(entity.id),
                    "canonical_name": entity.canonical_name,
                    "kind": entity.kind,
                    "primary_attributes": summary.primary_attributes or {},
                    "confidence_avg": summary.confidence_avg,
                    "connection_count": summary.connection_count,
                    "source_count": summary.source_count,
                    "source_mix": source_mix,
                    "conflict_count": _entity_conflict_count(session, entity.id),
                    "last_updated": summary.last_updated.isoformat(),
                }
                for summary, entity, source_mix in page_rows
            ],
        }


def entity_detail(store: Store, entity_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            return None
        summary = session.get(EntitySummary, entity_id)
        aliases = list(
            session.execute(
                select(EntityAlias.alias).where(EntityAlias.entity_id == entity_id)
            ).scalars()
        )
        neighborhoods = list(
            session.execute(
                select(EntityNeighborhood, Entity)
                .join(Entity, Entity.id == EntityNeighborhood.neighbor_id)
                .where(EntityNeighborhood.entity_id == entity_id)
                .order_by(EntityNeighborhood.confidence.desc())
            ).all()
        )
        attributes = _attribute_claims(session, entity_id)
        claim_count = session.execute(
            select(func.count())
            .select_from(Claim)
            .where(or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id))
        ).scalar_one()
        return {
            "identity": {
                "entity_id": str(entity.id),
                "canonical_name": entity.canonical_name,
                "kind": entity.kind,
                "aliases": aliases,
                "external_ids": {},
            },
            "primary_attributes": summary.primary_attributes if summary else {},
            "connections": [
                {
                    "neighbor_id": str(neighbor.id),
                    "neighbor_name": neighbor.canonical_name,
                    "neighbor_kind": neighbor.kind,
                    "predicates": row.predicates,
                    "confidence": row.confidence,
                    "evidence_count": row.evidence_count,
                    "is_resolved": True,
                }
                for row, neighbor in neighborhoods
            ],
            "attributes": attributes,
            "claim_count": claim_count,
            "conflict_count": _entity_conflict_count(session, entity_id),
            "last_updated": (
                summary.last_updated.isoformat() if summary else entity.updated_at.isoformat()
            ),
        }


def claim_detail(store: Store, claim_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        claim = session.get(Claim, claim_id)
        if claim is None:
            return None
        subject = session.get(Entity, claim.subject_entity_id)
        obj = session.get(Entity, claim.object_entity_id) if claim.object_entity_id else None
        evidence = _claim_evidence(session, claim.id)
        return {
            "claim_id": str(claim.id),
            "subject_entity_id": str(claim.subject_entity_id),
            "subject_name": subject.canonical_name if subject else None,
            "predicate": claim.predicate,
            "object_entity_id": str(claim.object_entity_id) if claim.object_entity_id else None,
            "object_name": obj.canonical_name if obj else claim.object_value,
            "object_value": claim.object_value,
            "qualifiers": claim.qualifiers,
            "confidence_score": claim.confidence_score,
            "status": claim.status,
            "evidence": evidence,
        }


async def ask_stream(
    store: Store,
    *,
    question: str,
    max_results: int = 10,
) -> AsyncIterator[bytes]:
    key = " ".join(question.casefold().split())
    cached = _ASK_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < ASK_CACHE_SECONDS:
        answer, citations = cached[1], cached[2]
    else:
        answer, citations = await _answer_from_graph(store, question, max_results=max_results)
        _ASK_CACHE[key] = (time.monotonic(), answer, citations)
        while len(_ASK_CACHE) > ASK_CACHE_MAX:
            _ASK_CACHE.popitem(last=False)
    for token in answer.split(" "):
        if token:
            yield _sse({"kind": "token", "text": token + " "})
            await asyncio.sleep(0)
    yield _sse({"kind": "citations", "citations": citations})
    yield _sse({"kind": "done"})


def write_feedback(
    store: Store,
    *,
    target_type: str,
    target_id: uuid.UUID,
    signal_type: str,
    payload: dict[str, object] | None,
    user_id: str = "site-user",
) -> uuid.UUID:
    with store.session() as session:
        signal = HumanSignal(
            signal_type=signal_type,
            target_type=target_type,
            target_id=target_id,
            user_id=user_id,
            payload=payload,
        )
        session.add(signal)
        session.commit()
        signal_id = signal.id
    if target_type == "claim":
        rescore_claim(store, target_id)
    return signal_id


_KIND_ICONS = {
    "domain": "ti-world",
    "file": "ti-file-spreadsheet",
    "api": "ti-api",
    "human": "ti-user",
}


def _source_status(source: Source) -> str:
    notes = source.notes or ""
    if notes.startswith("status:archived"):
        return "archived"
    if notes.startswith("status:paused"):
        return "paused"
    return "active"


def list_sources(store: Store) -> list[dict[str, object]]:
    with store.session() as session:
        sources = list(
            session.execute(
                select(Source).order_by(
                    Source.created_at.asc().nullslast(), Source.identifier.asc()
                )
            ).scalars()
        )
        output = []
        for source in sources:
            runs = list(
                session.execute(
                    select(SourceRun)
                    .where(SourceRun.source_id == source.id)
                    .order_by(SourceRun.started_at.desc())
                    .limit(5)
                ).scalars()
            )
            last_run = runs[0] if runs else None
            output.append(
                {
                    "id": str(source.id),
                    "kind": source.kind,
                    "identifier": source.identifier,
                    "display_name": source.display_name or source.identifier,
                    "trust_weight": source.trust_weight,
                    "status": _source_status(source),
                    "notes": source.notes,
                    "icon_hint": _KIND_ICONS.get(source.kind, "ti-database"),
                    "last_run_at": last_run.started_at.isoformat() if last_run else None,
                    "last_status": last_run.status if last_run else None,
                    "runs": [
                        {
                            "id": str(run.id),
                            "kind": run.kind,
                            "status": run.status,
                            "stats": run.stats,
                            "started_at": run.started_at.isoformat(),
                            "finished_at": (
                                run.finished_at.isoformat() if run.finished_at else None
                            ),
                            "error_message": run.error_message,
                        }
                        for run in runs
                    ],
                    "coverage": _source_coverage(session, source.id),
                }
            )
        return output


def source_detail(store: Store, source_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return None
        runs = list(
            session.execute(
                select(SourceRun)
                .where(SourceRun.source_id == source.id)
                .order_by(SourceRun.started_at.desc())
                .limit(50)
            ).scalars()
        )
        coverage = _source_coverage(session, source.id)
        document_count = session.execute(
            select(func.count(func.distinct(DocumentFetch.document_id)))
            .select_from(DocumentFetch)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source.id)
        ).scalar_one()
        return {
            "id": str(source.id),
            "kind": source.kind,
            "identifier": source.identifier,
            "display_name": source.display_name or source.identifier,
            "trust_weight": source.trust_weight,
            "status": _source_status(source),
            "notes": source.notes,
            "icon_hint": _KIND_ICONS.get(source.kind, "ti-database"),
            "created_at": source.created_at.isoformat(),
            "document_count": document_count,
            "coverage": coverage,
            "runs": [
                {
                    "id": str(run.id),
                    "kind": run.kind,
                    "status": run.status,
                    "spec": run.spec,
                    "stats": run.stats,
                    "started_at": run.started_at.isoformat(),
                    "finished_at": (run.finished_at.isoformat() if run.finished_at else None),
                    "error_message": run.error_message,
                    "triggered_by": run.triggered_by,
                }
                for run in runs
            ],
        }


def list_source_documents(
    store: Store,
    source_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with store.session() as session:
        base_query = (
            select(Document, Fetch)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
            .order_by(Document.created_at.desc())
        )
        total = session.execute(
            select(func.count(func.distinct(Document.id)))
            .select_from(DocumentFetch)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        ).scalar_one()
        rows = list(
            session.execute(base_query.offset((page - 1) * page_size).limit(page_size)).all()
        )
        seen: set[uuid.UUID] = set()
        results = []
        for document, fetch in rows:
            if document.id in seen:
                continue
            seen.add(document.id)
            chunks_count = session.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
            ).scalar_one()
            claims_extracted = session.execute(
                select(func.count())
                .select_from(ClaimRaw)
                .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
                .where(Chunk.document_id == document.id)
            ).scalar_one()
            results.append(
                {
                    "document_id": str(document.id),
                    "title": document.title or fetch.url,
                    "url": document.canonical_url or fetch.url,
                    "fetched_at": fetch.fetched_at.isoformat(),
                    "size_bytes": fetch.bytes_size or 0,
                    "word_count": document.word_count or 0,
                    "chunks": chunks_count,
                    "claims_extracted": claims_extracted,
                }
            )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "results": results,
        }


def document_detail(store: Store, document_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        fetch = session.get(Fetch, document.first_seen_fetch_id)
        chunks = list(
            session.execute(
                select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal.asc())
            ).scalars()
        )
        claims_raw = list(
            session.execute(
                select(ClaimRaw)
                .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
                .where(Chunk.document_id == document.id)
            ).scalars()
        )
        return {
            "document_id": str(document.id),
            "title": document.title,
            "url": document.canonical_url or (fetch.url if fetch else None),
            "fetched_at": fetch.fetched_at.isoformat() if fetch else None,
            "cleaned_text": document.cleaned_text,
            "word_count": document.word_count,
            "language": document.language,
            "chunks": [
                {
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "token_count": chunk.token_count,
                }
                for chunk in chunks
            ],
            "claims_raw": [
                {
                    "id": str(raw.id),
                    "subject_text": raw.subject_text,
                    "predicate": raw.predicate,
                    "object_text": raw.object_text,
                    "raw_quote": raw.raw_quote,
                    "confidence_internal": raw.confidence_internal,
                }
                for raw in claims_raw
            ],
        }


def source_breakdown(store: Store) -> list[dict[str, object]]:
    sources = list_sources(store)
    return [
        {
            "source_id": source["id"],
            "display_name": source["display_name"],
            "kind": source["kind"],
            "documents": source["coverage"]["documents"],
            "claims": source["coverage"]["claims"],
            "entities_contributed": _entities_contributed(store, uuid.UUID(source["id"])),
            "conflicts_caused": source["coverage"].get("conflicts", 0),
            "last_run_at": source["last_run_at"],
            "last_status": source["last_status"],
        }
        for source in sources
    ]


def _entities_contributed(store: Store, source_id: uuid.UUID) -> int:
    with store.session() as session:
        return session.execute(
            select(func.count(func.distinct(Claim.subject_entity_id)))
            .select_from(ClaimEvidence)
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .where(ClaimEvidence.source_id == source_id)
        ).scalar_one()


def update_source(
    store: Store,
    source_id: uuid.UUID,
    *,
    display_name: str | None = None,
    trust_weight: float | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> dict[str, object] | None:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return None
        if display_name is not None:
            source.display_name = display_name
        if trust_weight is not None:
            source.trust_weight = trust_weight
        if status is not None:
            existing_notes = source.notes or ""
            note_body = existing_notes
            if existing_notes.startswith("status:"):
                _, _, after = existing_notes.partition("\n")
                note_body = after
            if status == "active":
                source.notes = note_body or None
            else:
                source.notes = (
                    f"status:{status}\n{note_body}".strip() if note_body else f"status:{status}"
                )
        if notes is not None and status is None:
            existing_status_line = ""
            if source.notes and source.notes.startswith("status:"):
                first_line, _, _ = source.notes.partition("\n")
                existing_status_line = first_line + "\n"
            source.notes = f"{existing_status_line}{notes}".strip() or None
        session.commit()
    return source_detail(store, source_id)


def delete_source(store: Store, source_id: uuid.UUID) -> bool:
    return update_source(store, source_id, status="archived") is not None


def admin_corpus_stats(store: Store) -> dict[str, int]:
    counts = store.table_counts()
    return {
        "documents": counts.get("documents", 0),
        "claims": counts.get("claims", 0),
        "entities": counts.get("entities", 0),
        "sources": counts.get("sources", 0),
        "source_runs": counts.get("source_runs", 0),
        "conflicts": counts.get("claim_conflicts", 0),
        "fetches": counts.get("fetches", 0),
        "chunks": counts.get("chunks", 0),
        "claims_raw": counts.get("claims_raw", 0),
    }


def list_conflicts(store: Store, page: int = 1, page_size: int = 25) -> dict[str, object]:
    with store.session() as session:
        query = select(ClaimConflict).where(
            or_(ClaimConflict.resolution == "unresolved", ClaimConflict.resolution.is_(None))
        )
        total = session.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        rows = list(
            session.execute(
                query.order_by(ClaimConflict.detected_at.desc())
                .offset((max(page, 1) - 1) * page_size)
                .limit(page_size)
            ).scalars()
        )
        return {
            "total": total,
            "results": [
                {
                    "id": str(row.id),
                    "claim_a_id": str(row.claim_a_id),
                    "claim_b_id": str(row.claim_b_id),
                    "detected_at": row.detected_at.isoformat(),
                    "resolution": row.resolution,
                    "notes": row.notes,
                }
                for row in rows
            ],
        }


def resolve_conflict(
    store: Store,
    *,
    conflict_id: uuid.UUID,
    resolution: str,
    notes: str | None,
) -> None:
    with store.session() as session:
        conflict = session.get(ClaimConflict, conflict_id)
        if conflict is None:
            return
        conflict.resolution = resolution
        conflict.notes = notes
        conflict.resolved_by = "admin"
        conflict.resolved_at = utc_now()
        losing = conflict.claim_b_id if resolution == "claim_a_wins" else conflict.claim_a_id
        if resolution in {"claim_a_wins", "claim_b_wins"}:
            session.add(
                HumanSignal(
                    signal_type="retract_claim",
                    target_type="claim",
                    target_id=losing,
                    user_id="admin",
                    payload={"conflict_id": str(conflict.id), "notes": notes},
                )
            )
        session.commit()


def update_source_trust(store: Store, source_id: uuid.UUID, trust_weight: float) -> None:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return
        source.trust_weight = trust_weight
        session.commit()


def reset_extraction(store: Store) -> None:
    with store.session() as session:
        for model in (
            EntityNeighborhood,
            EntitySummary,
            ClaimConflict,
            ClaimEvidence,
            Claim,
            EntityMention,
            ClaimRaw,
            Chunk,
            DocumentFetch,
            Document,
        ):
            session.execute(delete(model))
        session.commit()


def _source_mix(session, entity_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(Source.identifier, func.count())
        .join(ClaimEvidence, ClaimEvidence.source_id == Source.id)
        .join(Claim, Claim.id == ClaimEvidence.claim_id)
        .where(or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id))
        .group_by(Source.identifier)
    ).all()
    return {identifier: count for identifier, count in rows}


def _entity_conflict_count(session, entity_id: uuid.UUID) -> int:
    claim_ids = select(Claim.id).where(
        or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id)
    )
    return session.execute(
        select(func.count())
        .select_from(ClaimConflict)
        .where(
            or_(
                ClaimConflict.claim_a_id.in_(claim_ids),
                ClaimConflict.claim_b_id.in_(claim_ids),
            )
        )
    ).scalar_one()


def _attribute_claims(session, entity_id: uuid.UUID) -> dict[str, list[dict[str, object]]]:
    claims = list(
        session.execute(
            select(Claim)
            .where(Claim.subject_entity_id == entity_id)
            .where(Claim.object_entity_id.is_(None))
            .order_by(Claim.confidence_score.desc())
        ).scalars()
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    for claim in claims:
        grouped.setdefault(claim.predicate, []).append(
            {
                "claim_id": str(claim.id),
                "object_value": claim.object_value,
                "confidence_score": claim.confidence_score,
                "evidence": _claim_evidence(session, claim.id),
            }
        )
    return grouped


def _claim_evidence(session, claim_id: uuid.UUID) -> list[dict[str, object]]:
    rows = list(
        session.execute(
            select(ClaimEvidence, Source, ClaimRaw, Fetch)
            .join(Source, Source.id == ClaimEvidence.source_id)
            .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
            .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .where(ClaimEvidence.claim_id == claim_id)
        ).all()
    )
    return [
        {
            "source_id": str(source.id),
            "source_identifier": source.identifier,
            "url": fetch.url,
            "weight": evidence.weight,
            "raw_quote": raw.raw_quote,
            "added_at": evidence.added_at.isoformat(),
            "audit_verdict": None,
        }
        for evidence, source, raw, fetch in rows
    ]


async def _answer_from_graph(
    store: Store,
    question: str,
    *,
    max_results: int,
) -> tuple[str, list[dict[str, object]]]:
    settings = get_settings()
    question_vector = await embed_text(question)
    with store.session() as session:
        chunks = _rank_chunks(
            list(session.execute(select(Chunk).limit(200)).scalars()),
            question_vector,
        )[:8]
        claims = list(
            session.execute(
                select(Claim).order_by(Claim.confidence_score.desc()).limit(max_results)
            ).scalars()
        )
        citations = []
        for claim in claims[:3]:
            evidence = _claim_evidence(session, claim.id)
            if evidence:
                citations.append(
                    {
                        "claim_id": str(claim.id),
                        "source_id": evidence[0]["source_id"],
                        "quote": evidence[0]["raw_quote"],
                    }
                )
    if not claims and not chunks:
        return (
            "No extracted graph evidence is available yet. Run ingestion and the pipeline first.",
            [],
        )
    if settings.openai_api_key:
        return await _llm_answer(
            question=question,
            chunks=chunks,
            claims=claims,
            citations=citations,
            model=settings.frontier_model,
            api_key=settings.openai_api_key,
        )
    return (
        f"Based on the current graph, I found {len(claims)} relevant claims for: {question}",
        citations,
    )


async def _llm_answer(
    *,
    question: str,
    chunks: list[Chunk],
    claims: list[Claim],
    citations: list[dict[str, object]],
    model: str,
    api_key: str,
) -> tuple[str, list[dict[str, object]]]:
    chunk_context = "\n\n".join(
        f"[chunk {index + 1}] {chunk.text}" for index, chunk in enumerate(chunks)
    )
    claim_context = "\n".join(
        (
            f"- claim_id={claim.id} predicate={claim.predicate} "
            f"confidence={claim.confidence_score:.2f} "
            f"subject={claim.subject_entity_id} "
            f"object={claim.object_entity_id or claim.object_value}"
        )
        for claim in claims
    )
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Answer questions using only the supplied Pinegraf chunks and graph claims. "
                    "Cite claim ids or source snippets when making factual statements. "
                    "If the evidence is insufficient, say so plainly."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\n"
                    f"Graph claims:\n{claim_context or 'none'}\n\n"
                    f"Retrieved chunks:\n{chunk_context or 'none'}"
                ),
            },
        ],
        temperature=0,
    )
    content = response.choices[0].message.content or ""
    return content.strip() or "No answer could be generated from the available evidence.", citations


def _rank_chunks(chunks: list[Chunk], question_vector: list[float]) -> list[Chunk]:
    if not chunks:
        return []
    if not any(question_vector):
        return chunks
    return sorted(
        chunks,
        key=lambda chunk: _cosine(question_vector, chunk.embedding or []),
        reverse=True,
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = sum(value * value for value in left[:size]) ** 0.5
    right_norm = sum(value * value for value in right[:size]) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _source_coverage(session, source_id: uuid.UUID) -> dict[str, int]:
    documents = session.execute(
        select(func.count(func.distinct(DocumentFetch.document_id)))
        .select_from(DocumentFetch)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .where(SourceRun.source_id == source_id)
    ).scalar_one()
    claims = session.execute(
        select(func.count()).select_from(ClaimEvidence).where(ClaimEvidence.source_id == source_id)
    ).scalar_one()
    claim_ids = select(ClaimEvidence.claim_id).where(ClaimEvidence.source_id == source_id)
    conflicts = session.execute(
        select(func.count())
        .select_from(ClaimConflict)
        .where(
            or_(
                ClaimConflict.claim_a_id.in_(claim_ids),
                ClaimConflict.claim_b_id.in_(claim_ids),
            )
        )
    ).scalar_one()
    return {"documents": documents, "claims": claims, "conflicts": conflicts}


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
