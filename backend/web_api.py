from __future__ import annotations

import json
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException
from openai import AsyncOpenAI
from sqlalchemy import and_, delete, func, or_, select, update

from backend.class_year import expand_class_year_synonyms
from backend.config import Settings, get_settings
from backend.db.models import (
    AuditLog,
    Chunk,
    Claim,
    ClaimConflict,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Entity,
    EntityAlias,
    EntityDisambiguationCandidate,
    EntityMention,
    EntityNeighborhood,
    EntitySummary,
    Fetch,
    HumanSignal,
    Source,
    SourceRun,
)
from backend.db.stats_queries import (
    documents_for_source,
    global_stats,
    source_coverage,
)
from backend.db.store import Store, utc_now
from backend.resolution.embedder import embed_texts
from backend.util.vector import cosine, vector_values

ASK_CACHE_SECONDS = 3600
ASK_CACHE_MAX = 100
ACTIVE_SOURCE_RUN_STATUSES = {"queued", "running"}
STOPPED_SOURCE_RUN_STATUS = "stopped"
_ASK_CACHE: OrderedDict[str, tuple[float, str, list[dict[str, object]]]] = OrderedDict()


class ActiveSourceRunError(RuntimeError):
    def __init__(self, run_id: uuid.UUID, status: str) -> None:
        super().__init__("source has an active run")
        self.run_id = run_id
        self.status = status


def _source_run_action_kind(run: SourceRun) -> str:
    if run.kind == "parse":
        return "parse"
    return "crawl"


def _active_runs_payload(runs: list[SourceRun]) -> dict[str, dict[str, object]]:
    active: dict[str, dict[str, object]] = {}
    for run in runs:
        if run.status not in ACTIVE_SOURCE_RUN_STATUSES:
            continue
        active[_source_run_action_kind(run)] = {
            "id": str(run.id),
            "kind": run.kind,
            "action": _source_run_action_kind(run),
            "status": run.status,
            "stats": run.stats,
            "started_at": run.started_at.isoformat(),
        }
    return active


def _paused_runs_payload(runs: list[SourceRun]) -> dict[str, dict[str, object]]:
    paused: dict[str, dict[str, object]] = {}
    for run in sorted(runs, key=lambda value: value.started_at, reverse=True):
        if run.status != STOPPED_SOURCE_RUN_STATUS:
            continue
        action = _source_run_action_kind(run)
        if action in paused:
            continue
        paused[action] = {
            "id": str(run.id),
            "kind": run.kind,
            "action": action,
            "status": run.status,
            "stats": run.stats,
            "started_at": run.started_at.isoformat(),
        }
    return paused


def stats(store: Store) -> dict[str, int]:
    with store.session() as session:
        return global_stats(session)


def list_audit_log(store: Store, *, limit: int = 200) -> dict[str, object]:
    limit = min(max(limit, 1), 200)
    with store.session() as session:
        rows = list(
            session.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).scalars()
        )
    return {
        "entries": [
            {
                "id": str(row.id),
                "ts": row.ts.isoformat(),
                "action": row.action,
                "target_table": row.target_table,
                "target_id": row.target_id,
                "actor": row.actor,
                "request_ip": row.request_ip,
                "payload": row.payload,
            }
            for row in rows
        ]
    }


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
    org_filters = _csv_filter(org)
    class_year_filters = _csv_filter(class_year)
    source_filters = _csv_filter(source)
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
            if org_filters and not any(value in haystack for value in org_filters):
                continue
            if class_year_filters and not any(value in haystack for value in class_year_filters):
                continue
            if summary.confidence_avg is not None and summary.confidence_avg < min_confidence:
                continue
            source_mix = _source_mix(session, entity.id)
            source_keys = {identifier.casefold() for identifier in source_mix}
            if source_filters and not any(value in source_keys for value in source_filters):
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
                    "primary_attribute_claims": _primary_attribute_claims(
                        session, entity.id, summary.primary_attributes or {}
                    ),
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


def _csv_filter(value: str) -> list[str]:
    return [
        part.casefold()
        for part in (item.strip() for item in value.split(","))
        if part and part.casefold() != "all"
    ]


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
                    "claims": _connection_claims(
                        session,
                        entity_id,
                        neighbor.id,
                        row.predicates or [],
                    ),
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


def list_claims(
    store: Store,
    *,
    predicate: str = "",
    subject_entity_id: uuid.UUID | None = None,
    object_entity_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    min_confidence: float = 0.0,
    status: str = "current",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    status = status if status in {"all", "current", "superseded"} else "current"
    with store.session() as session:
        subject_alias = Entity.__table__.alias("subject_entity")
        object_alias = Entity.__table__.alias("object_entity")
        query = (
            select(Claim)
            .join(subject_alias, subject_alias.c.id == Claim.subject_entity_id)
            .outerjoin(object_alias, object_alias.c.id == Claim.object_entity_id)
        )
        conditions = []
        if predicate:
            conditions.append(Claim.predicate == predicate)
        if subject_entity_id is not None:
            conditions.append(Claim.subject_entity_id == subject_entity_id)
        if object_entity_id is not None:
            conditions.append(Claim.object_entity_id == object_entity_id)
        if source_id is not None:
            conditions.append(
                select(ClaimEvidence.claim_id)
                .where(ClaimEvidence.claim_id == Claim.id)
                .where(ClaimEvidence.source_id == source_id)
                .exists()
            )
        if min_confidence:
            conditions.append(Claim.confidence_score >= min_confidence)
        if status == "current":
            conditions.append(Claim.valid_to.is_(None))
        elif status == "superseded":
            conditions.append(Claim.valid_to.is_not(None))
        if q:
            pattern = f"%{q}%"
            conditions.append(
                or_(
                    subject_alias.c.canonical_name.ilike(pattern),
                    object_alias.c.canonical_name.ilike(pattern),
                    Claim.object_value.ilike(pattern),
                )
            )
        if conditions:
            query = query.where(and_(*conditions))
        total = session.execute(
            select(func.count()).select_from(query.order_by(None).subquery())
        ).scalar_one()
        rows = list(
            session.execute(
                query.order_by(Claim.confidence_score.desc(), Claim.first_seen_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars()
        )
        return {
            "claims": [_claim_response(session, claim, include_sources=True) for claim in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "filters_applied": {
                "predicate": predicate or None,
                "subject_entity_id": str(subject_entity_id) if subject_entity_id else None,
                "object_entity_id": str(object_entity_id) if object_entity_id else None,
                "source_id": str(source_id) if source_id else None,
                "min_confidence": min_confidence,
                "status": status,
                "q": q or None,
            },
        }


def claim_detail(store: Store, claim_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        claim = session.get(Claim, claim_id)
        if claim is None:
            return None
        response = _claim_response(session, claim, include_sources=True)
        response["evidence"] = _claim_evidence(session, claim.id)
        response["subject_entity"] = _entity_record(session, claim.subject_entity_id)
        response["object_entity"] = (
            _entity_record(session, claim.object_entity_id) if claim.object_entity_id else None
        )
        response["superseded_by"] = (
            _claim_response(session, session.get(Claim, claim.superseded_by_claim_id))
            if claim.superseded_by_claim_id
            else None
        )
        return response


def claim_predicates(store: Store) -> list[str]:
    with store.session() as session:
        return list(
            session.execute(
                select(Claim.predicate).distinct().order_by(Claim.predicate.asc())
            ).scalars()
        )


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
        if answer:
            yield _sse({"kind": "token", "text": answer})
        yield _sse({"kind": "citations", "citations": citations})
        yield _sse({"kind": "done"})
        return

    settings, chunks, claims, citations = await _answer_materials(
        store, question, max_results=max_results
    )
    answer = ""
    if not claims and not chunks:
        answer = "No extracted graph evidence is available yet. Run ingestion and parse first."
        yield _sse({"kind": "token", "text": answer})
    elif settings.openai_api_key:
        parts: list[str] = []
        async for token in _llm_answer_tokens(
            question=question,
            chunks=chunks,
            claims=claims,
            model=settings.frontier_model,
            api_key=settings.openai_api_key,
        ):
            parts.append(token)
            yield _sse({"kind": "token", "text": token})
        answer = "".join(parts).strip()
        if not answer:
            answer = "No answer could be generated from the available evidence."
            yield _sse({"kind": "token", "text": answer})
    else:
        answer = (
            f"Based on the current graph, I found {len(claims)} relevant claims for: {question}"
        )
        yield _sse({"kind": "token", "text": answer})

    _ASK_CACHE[key] = (time.monotonic(), answer, citations)
    while len(_ASK_CACHE) > ASK_CACHE_MAX:
        _ASK_CACHE.popitem(last=False)
    yield _sse({"kind": "citations", "citations": citations})
    yield _sse({"kind": "done"})


_KIND_ICONS = {
    "domain": "ti-world",
    "file": "ti-file-spreadsheet",
}


def _source_is_archived(source: Source) -> bool:
    return source.status == "archived"


def archived_source_count(store: Store) -> int:
    with store.session() as session:
        return int(
            session.execute(
                select(func.count()).select_from(Source).where(Source.status == "archived")
            ).scalar_one()
        )


def list_sources(store: Store, *, include_archived: bool = False) -> list[dict[str, object]]:
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
            if _source_is_archived(source) and not include_archived:
                continue
            runs = list(
                session.execute(
                    select(SourceRun)
                    .where(SourceRun.source_id == source.id)
                    .order_by(SourceRun.started_at.desc())
                    .limit(5)
                ).scalars()
            )
            last_run = runs[0] if runs else None
            active_run = next(
                (run for run in runs if run.status in ACTIVE_SOURCE_RUN_STATUSES),
                None,
            )
            active_runs = _active_runs_payload(runs)
            paused_runs = _paused_runs_payload(runs)
            coverage = source_coverage(session, source.id)
            if active_runs.get("parse"):
                coverage["pending_parse_count"] = 0
            output.append(
                {
                    "id": str(source.id),
                    "kind": source.kind,
                    "identifier": source.identifier,
                    "display_name": source.display_name or source.identifier,
                    "trust_weight": source.trust_weight,
                    "respect_robots": source.respect_robots,
                    "status": source.status,
                    "pages_fetched_total": source.pages_fetched_total,
                    "urls_known_total": source.urls_known_total,
                    "recrawl_interval_days": source.recrawl_interval_days,
                    "last_full_recrawl_at": (
                        source.last_full_recrawl_at.isoformat()
                        if source.last_full_recrawl_at
                        else None
                    ),
                    "notes": source.notes,
                    "icon_hint": _KIND_ICONS.get(source.kind, "ti-database"),
                    "last_run_at": last_run.started_at.isoformat() if last_run else None,
                    "last_status": last_run.status if last_run else None,
                    "active_run_id": str(active_run.id) if active_run else None,
                    "active_run_kind": _source_run_action_kind(active_run) if active_run else None,
                    "active_runs": active_runs,
                    "paused_runs": paused_runs,
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
                    "pending_parse_count": coverage["pending_parse_count"],
                    "coverage": coverage,
                }
            )
        return output


def source_detail(
    store: Store,
    source_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> dict[str, object] | None:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None or (_source_is_archived(source) and not include_archived):
            return None
        file_size_bytes = None
        if source.kind == "file":
            upload_path = Path(get_settings().uploads_dir) / source.identifier
            if upload_path.is_file():
                file_size_bytes = upload_path.stat().st_size
        runs = list(
            session.execute(
                select(SourceRun)
                .where(SourceRun.source_id == source.id)
                .order_by(SourceRun.started_at.desc())
                .limit(50)
            ).scalars()
        )
        active_run = next(
            (run for run in runs if run.status in ACTIVE_SOURCE_RUN_STATUSES),
            None,
        )
        active_runs = _active_runs_payload(runs)
        paused_runs = _paused_runs_payload(runs)
        coverage = source_coverage(session, source.id)
        if active_runs.get("parse"):
            coverage["pending_parse_count"] = 0
        document_count = documents_for_source(session, source.id)
        latest_parse = next((run for run in runs if run.kind == "parse" and run.finished_at), None)
        return {
            "id": str(source.id),
            "kind": source.kind,
            "identifier": source.identifier,
            "display_name": source.display_name or source.identifier,
            "trust_weight": source.trust_weight,
            "respect_robots": source.respect_robots,
            "status": source.status,
            "pages_fetched_total": source.pages_fetched_total,
            "urls_known_total": source.urls_known_total,
            "recrawl_interval_days": source.recrawl_interval_days,
            "last_full_recrawl_at": (
                source.last_full_recrawl_at.isoformat() if source.last_full_recrawl_at else None
            ),
            "notes": source.notes,
            "file_size_bytes": file_size_bytes,
            "icon_hint": _KIND_ICONS.get(source.kind, "ti-database"),
            "created_at": source.created_at.isoformat(),
            "document_count": document_count,
            "last_run_at": runs[0].started_at.isoformat() if runs else None,
            "last_status": runs[0].status if runs else None,
            "active_run_id": str(active_run.id) if active_run else None,
            "active_run_kind": _source_run_action_kind(active_run) if active_run else None,
            "active_runs": active_runs,
            "paused_runs": paused_runs,
            "pending_parse_count": coverage["pending_parse_count"],
            "latest_parse_finished_at": (
                latest_parse.finished_at.isoformat()
                if latest_parse and latest_parse.finished_at
                else None
            ),
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
                    "fetch_id": str(fetch.id),
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
        source = None
        if fetch is not None:
            source = session.execute(
                select(Source)
                .join(SourceRun, SourceRun.source_id == Source.id)
                .where(SourceRun.id == fetch.source_run_id)
            ).scalar_one_or_none()
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
                    "evidence": [
                        {
                            "source_id": str(source.id),
                            "source_identifier": source.identifier,
                            "source_name": source.display_name or source.identifier,
                            "document_id": str(document.id),
                            "document_title": document.title,
                            "document_url": document.canonical_url or (fetch.url if fetch else ""),
                            "url": fetch.url if fetch else "",
                            "live_url": document.canonical_url or (fetch.url if fetch else ""),
                            "fetched_at": fetch.fetched_at.isoformat() if fetch else None,
                            "raw_quote": raw.raw_quote,
                        }
                    ]
                    if source is not None
                    else [],
                }
                for raw in claims_raw
            ],
        }


def update_source(
    store: Store,
    source_id: uuid.UUID,
    *,
    display_name: str | None = None,
    trust_weight: float | None = None,
    respect_robots: bool | None = None,
    status: str | None = None,
    notes: str | None = None,
    recrawl_interval_days: int | None = None,
    audit_actor: str | None = None,
    audit_request_ip: str | None = None,
    audit_payload: dict[str, object] | None = None,
) -> dict[str, object] | None:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return None
        if display_name is not None:
            source.display_name = display_name
        if trust_weight is not None:
            source.trust_weight = trust_weight
        if respect_robots is not None:
            source.respect_robots = respect_robots
        if status is not None:
            source.status = status
        if notes is not None:
            source.notes = notes.strip() or None
        if recrawl_interval_days is not None:
            source.recrawl_interval_days = max(1, int(recrawl_interval_days))
        if audit_payload is not None:
            _add_audit_row(
                session,
                action="source.update",
                target_table="sources",
                target_id=source_id,
                actor=audit_actor,
                request_ip=audit_request_ip,
                payload=audit_payload,
            )
        session.commit()
    return source_detail(store, source_id, include_archived=True)


def delete_source(
    store: Store,
    source_id: uuid.UUID,
    *,
    audit_actor: str | None = None,
    audit_request_ip: str | None = None,
    audit_payload: dict[str, object] | None = None,
) -> bool:
    """Hard-delete a source and all derived data. Returns True if deleted."""
    uploaded_filename = None
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return False
        active_run = session.execute(
            select(SourceRun)
            .where(SourceRun.source_id == source_id)
            .where(SourceRun.status.in_(ACTIVE_SOURCE_RUN_STATUSES))
            .order_by(SourceRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if active_run is not None:
            raise ActiveSourceRunError(active_run.id, active_run.status)
        uploaded_filename = source.identifier if source.kind == "file" else None

        fetch_ids = list(
            session.execute(
                select(Fetch.id)
                .join(SourceRun, SourceRun.id == Fetch.source_run_id)
                .where(SourceRun.source_id == source_id)
            ).scalars()
        )
        orphan_document_ids = _orphan_document_ids_for_fetches(session, fetch_ids)
        _repoint_shared_documents(session, fetch_ids, orphan_document_ids)
        orphan_chunk_ids = _chunk_ids_for_documents(session, orphan_document_ids)
        orphan_claim_raw_ids = _claim_raw_ids_for_chunks(session, orphan_chunk_ids)

        if orphan_claim_raw_ids:
            _delete_rows(
                session,
                delete(EntityMention).where(EntityMention.claim_raw_id.in_(orphan_claim_raw_ids)),
            )
            _delete_rows(
                session,
                delete(ClaimEvidence).where(ClaimEvidence.claim_raw_id.in_(orphan_claim_raw_ids)),
            )
        _delete_rows(session, delete(ClaimEvidence).where(ClaimEvidence.source_id == source_id))

        if orphan_claim_raw_ids:
            _delete_rows(
                session,
                delete(ClaimRaw).where(ClaimRaw.id.in_(orphan_claim_raw_ids)),
            )

        _delete_claims_without_evidence(session)
        _delete_entities_without_refs(session, require_no_aliases=False)

        if orphan_chunk_ids:
            _delete_rows(session, delete(Chunk).where(Chunk.id.in_(orphan_chunk_ids)))
        if orphan_document_ids:
            _delete_rows(
                session,
                delete(DocumentFetch).where(DocumentFetch.document_id.in_(orphan_document_ids)),
            )
            _delete_rows(
                session,
                delete(Document).where(Document.id.in_(orphan_document_ids)),
            )
        if fetch_ids:
            _delete_rows(
                session, delete(DocumentFetch).where(DocumentFetch.fetch_id.in_(fetch_ids))
            )
            _delete_rows(session, delete(Fetch).where(Fetch.id.in_(fetch_ids)))
        _delete_rows(session, delete(SourceRun).where(SourceRun.source_id == source_id))
        if audit_payload is not None:
            _add_audit_row(
                session,
                action="source.delete",
                target_table="sources",
                target_id=source_id,
                actor=audit_actor,
                request_ip=audit_request_ip,
                payload=audit_payload,
            )
        session.delete(source)
        session.commit()

    if uploaded_filename:
        _delete_uploaded_file(uploaded_filename)
    return True


def _orphan_document_ids_for_fetches(session, fetch_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not fetch_ids:
        return []
    candidate_doc_ids = list(
        session.execute(
            select(Document.id).where(Document.first_seen_fetch_id.in_(fetch_ids))
        ).scalars()
    )
    orphan_document_ids = []
    for document_id in candidate_doc_ids:
        other_refs = session.execute(
            select(func.count())
            .select_from(DocumentFetch)
            .where(DocumentFetch.document_id == document_id)
            .where(~DocumentFetch.fetch_id.in_(fetch_ids))
        ).scalar_one()
        if other_refs == 0:
            orphan_document_ids.append(document_id)
    return orphan_document_ids


def _chunk_ids_for_documents(session, document_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not document_ids:
        return []
    return list(
        session.execute(select(Chunk.id).where(Chunk.document_id.in_(document_ids))).scalars()
    )


def _repoint_shared_documents(
    session,
    fetch_ids: list[uuid.UUID],
    orphan_document_ids: list[uuid.UUID],
) -> None:
    if not fetch_ids:
        return
    orphan_set = set(orphan_document_ids)
    shared_document_ids = list(
        session.execute(
            select(Document.id).where(Document.first_seen_fetch_id.in_(fetch_ids))
        ).scalars()
    )
    for document_id in shared_document_ids:
        if document_id in orphan_set:
            continue
        replacement_fetch_id = session.execute(
            select(DocumentFetch.fetch_id)
            .where(DocumentFetch.document_id == document_id)
            .where(~DocumentFetch.fetch_id.in_(fetch_ids))
            .limit(1)
        ).scalar_one_or_none()
        if replacement_fetch_id is not None:
            document = session.get(Document, document_id)
            if document is not None:
                document.first_seen_fetch_id = replacement_fetch_id


def _claim_raw_ids_for_chunks(session, chunk_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not chunk_ids:
        return []
    return list(
        session.execute(select(ClaimRaw.id).where(ClaimRaw.chunk_id.in_(chunk_ids))).scalars()
    )


def _delete_claims_without_evidence(session) -> int:
    orphan_claim_ids = list(
        session.execute(
            select(Claim.id).where(
                ~select(ClaimEvidence.claim_id).where(ClaimEvidence.claim_id == Claim.id).exists()
            )
        ).scalars()
    )
    if not orphan_claim_ids:
        return 0
    _delete_rows(
        session,
        delete(ClaimConflict).where(
            or_(
                ClaimConflict.claim_a_id.in_(orphan_claim_ids),
                ClaimConflict.claim_b_id.in_(orphan_claim_ids),
            )
        ),
    )
    _delete_rows(session, delete(Claim).where(Claim.id.in_(orphan_claim_ids)))
    return len(orphan_claim_ids)


def _delete_entities_without_refs(session, *, require_no_aliases: bool) -> int:
    query = (
        select(Entity.id)
        .where(
            ~select(EntityMention.entity_id).where(EntityMention.entity_id == Entity.id).exists()
        )
        .where(
            ~select(Claim.subject_entity_id).where(Claim.subject_entity_id == Entity.id).exists()
        )
        .where(~select(Claim.object_entity_id).where(Claim.object_entity_id == Entity.id).exists())
    )
    if require_no_aliases:
        query = query.where(
            ~select(EntityAlias.entity_id).where(EntityAlias.entity_id == Entity.id).exists()
        )
    orphan_entity_ids = list(session.execute(query).scalars())
    if not orphan_entity_ids:
        return 0
    _delete_rows(
        session,
        delete(EntityNeighborhood).where(
            or_(
                EntityNeighborhood.entity_id.in_(orphan_entity_ids),
                EntityNeighborhood.neighbor_id.in_(orphan_entity_ids),
            )
        ),
    )
    _delete_rows(
        session,
        delete(EntitySummary).where(EntitySummary.entity_id.in_(orphan_entity_ids)),
    )
    if not require_no_aliases:
        _delete_rows(
            session,
            delete(EntityAlias).where(EntityAlias.entity_id.in_(orphan_entity_ids)),
        )
    _delete_rows(session, delete(Entity).where(Entity.id.in_(orphan_entity_ids)))
    return len(orphan_entity_ids)


def _delete_uploaded_file(filename: str) -> bool:
    file_path = Path(get_settings().uploads_dir) / filename
    if not file_path.exists():
        return False
    try:
        file_path.unlink()
    except OSError:
        return False
    return True


def _delete_rows(session, statement) -> int:
    result = session.execute(statement.execution_options(synchronize_session=False))
    return int(result.rowcount or 0)


def _add_audit_row(
    session,
    *,
    action: str,
    target_table: str,
    target_id: uuid.UUID | str,
    actor: str | None,
    request_ip: str | None,
    payload: dict[str, object] | None,
) -> None:
    session.add(
        AuditLog(
            action=action,
            target_table=target_table,
            target_id=str(target_id),
            actor=actor,
            request_ip=request_ip,
            payload=payload,
        )
    )


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
                    "claim_a": _claim_to_dict(session, row.claim_a_id),
                    "claim_b": _claim_to_dict(session, row.claim_b_id),
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


def list_identity_review(
    store: Store,
    *,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, object]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    with store.session() as session:
        base = select(EntityDisambiguationCandidate).where(
            EntityDisambiguationCandidate.review_decision.is_(None)
        )
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = list(
            session.execute(
                base.order_by(EntityDisambiguationCandidate.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars()
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "results": [_identity_review_payload(session, row) for row in rows],
        }


def review_identity_candidate(
    store: Store,
    *,
    candidate_id: uuid.UUID,
    decision: str,
    reviewer: str | None,
    audit_actor: str | None,
    audit_request_ip: str | None,
) -> dict[str, object]:
    if decision not in {"confirm", "merge", "split"}:
        raise HTTPException(status_code=400, detail="decision must be confirm, merge, or split")
    with store.session() as session:
        row = session.get(EntityDisambiguationCandidate, candidate_id)
        if row is None:
            raise HTTPException(status_code=404, detail="identity review candidate not found")
        if row.review_decision is not None:
            raise HTTPException(
                status_code=409,
                detail="identity review candidate already reviewed",
            )
        if decision == "merge":
            result = _merge_identity_candidate(session, row)
        else:
            result = {"merged": False}
        row.review_decision = decision
        row.reviewed_by = reviewer or audit_actor or "admin"
        row.reviewed_at = utc_now()
        _add_audit_row(
            session,
            action=f"identity_review.{decision}",
            target_table="entity_disambiguation_candidates",
            target_id=row.id,
            actor=audit_actor,
            request_ip=audit_request_ip,
            payload={
                "reviewer": row.reviewed_by,
                "decision": decision,
                "candidate_entity_id": str(row.candidate_entity_id),
                **result,
            },
        )
        payload = _identity_review_payload(session, row)
        session.commit()
        return {"status": "ok", "candidate": payload, **result}


def _identity_review_payload(
    session,
    row: EntityDisambiguationCandidate,
) -> dict[str, object]:
    mention = session.get(EntityMention, row.mention_id) if row.mention_id else None
    candidate = session.get(Entity, row.candidate_entity_id)
    source_entity = session.get(Entity, mention.entity_id) if mention else None
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "llm_decision": row.llm_decision,
        "llm_reasoning": row.llm_reasoning,
        "name_similarity_score": row.name_similarity_score,
        "review_decision": row.review_decision,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "mention": (
            {
                "id": str(mention.id),
                "text": mention.mention_text,
                "position": mention.position,
                "entity_id": str(mention.entity_id),
            }
            if mention
            else None
        ),
        "source_entity": _entity_record(session, source_entity.id) if source_entity else None,
        "candidate_entity": _entity_record(session, candidate.id) if candidate else None,
        "source_qualifiers": _entity_review_qualifiers(session, source_entity.id)
        if source_entity
        else {},
        "candidate_qualifiers": _entity_review_qualifiers(session, row.candidate_entity_id),
    }


def _merge_identity_candidate(
    session,
    row: EntityDisambiguationCandidate,
) -> dict[str, object]:
    mention = session.get(EntityMention, row.mention_id) if row.mention_id else None
    target = session.get(Entity, row.candidate_entity_id)
    if mention is None:
        raise HTTPException(status_code=400, detail="review candidate has no mention to merge")
    source = session.get(Entity, mention.entity_id)
    if source is None or target is None:
        raise HTTPException(status_code=400, detail="review candidate references a missing entity")
    source_id = source.id
    target_id = target.id
    if source_id == target_id:
        return {
            "merged": False,
            "source_entity_id": str(source_id),
            "target_entity_id": str(target_id),
            "reason": "source entity already matches candidate",
        }
    _migrate_aliases(session, source, target)
    session.execute(
        update(Claim)
        .where(Claim.subject_entity_id == source_id)
        .values(subject_entity_id=target_id)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(Claim)
        .where(Claim.object_entity_id == source_id)
        .values(object_entity_id=target_id)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        update(EntityMention)
        .where(EntityMention.entity_id == source_id)
        .values(entity_id=target_id)
        .execution_options(synchronize_session=False)
    )
    session.execute(
        delete(EntityNeighborhood)
        .where(
            or_(
                EntityNeighborhood.entity_id == source_id,
                EntityNeighborhood.neighbor_id == source_id,
                EntityNeighborhood.entity_id == target_id,
                EntityNeighborhood.neighbor_id == target_id,
            )
        )
        .execution_options(synchronize_session=False)
    )
    source.superseded_by_entity_id = target_id
    source.updated_at = utc_now()
    target.updated_at = utc_now()
    return {
        "merged": True,
        "source_entity_id": str(source_id),
        "target_entity_id": str(target_id),
    }


def _migrate_aliases(session, source: Entity, target: Entity) -> None:
    target_aliases = {
        _alias_key(target.canonical_name),
        *(
            _alias_key(alias)
            for alias in session.execute(
                select(EntityAlias.alias).where(EntityAlias.entity_id == target.id)
            ).scalars()
        ),
    }
    source_name_key = _alias_key(source.canonical_name)
    if source_name_key and source_name_key not in target_aliases:
        session.add(
            EntityAlias(
                entity_id=target.id,
                alias=source.canonical_name,
                confidence=1.0,
                source="identity_review:merge",
            )
        )
        target_aliases.add(source_name_key)
    aliases = list(
        session.execute(select(EntityAlias).where(EntityAlias.entity_id == source.id)).scalars()
    )
    for alias in aliases:
        key = _alias_key(alias.alias)
        if key in target_aliases:
            session.delete(alias)
            continue
        alias.entity_id = target.id
        alias.source = alias.source or "identity_review:merge"
        target_aliases.add(key)


def _alias_key(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())


def _entity_review_qualifiers(session, entity_id: uuid.UUID) -> dict[str, list[str]]:
    qualifiers: dict[str, list[str]] = {}
    summary = session.get(EntitySummary, entity_id)
    if summary and isinstance(summary.primary_attributes, dict):
        for key, value in summary.primary_attributes.items():
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            qualifiers[str(key)] = [str(item) for item in values if item is not None]
    rows = list(
        session.execute(
            select(Claim.predicate, Entity.canonical_name, Claim.object_value)
            .outerjoin(Entity, Entity.id == Claim.object_entity_id)
            .where(Claim.subject_entity_id == entity_id)
            .where(
                Claim.predicate.in_(
                    [
                        "class_year",
                        "affiliated_with",
                        "employed_by",
                        "located_in",
                        "founded",
                        "worked_on_project",
                    ]
                )
            )
            .limit(50)
        ).all()
    )
    for predicate, object_name, object_value in rows:
        value = object_name or object_value
        if value is None:
            continue
        bucket = qualifiers.setdefault(predicate, [])
        text = str(value)
        if text not in bucket:
            bucket.append(text)
    return qualifiers


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


def _primary_attribute_claims(
    session,
    entity_id: uuid.UUID,
    primary_attributes: dict[str, object],
) -> dict[str, dict[str, object]]:
    if not primary_attributes:
        return {}
    grouped = _attribute_claims(session, entity_id)
    output: dict[str, dict[str, object]] = {}
    for key, value in primary_attributes.items():
        if value in (None, ""):
            continue
        claims = grouped.get(key) or []
        if claims:
            output[key] = claims[0]
    return output


def _connection_claims(
    session,
    entity_id: uuid.UUID,
    neighbor_id: uuid.UUID,
    predicates: list[str],
) -> list[dict[str, object]]:
    query = select(Claim).where(
        or_(
            (Claim.subject_entity_id == entity_id) & (Claim.object_entity_id == neighbor_id),
            (Claim.subject_entity_id == neighbor_id) & (Claim.object_entity_id == entity_id),
        )
    )
    if predicates:
        query = query.where(Claim.predicate.in_(predicates))
    claims = list(
        session.execute(query.order_by(Claim.confidence_score.desc()).limit(10)).scalars()
    )
    return [claim for claim in (_claim_to_dict(session, row.id) for row in claims) if claim]


def _claim_to_dict(session, claim_id: uuid.UUID) -> dict[str, object] | None:
    claim = session.get(Claim, claim_id)
    if claim is None:
        return None
    return _claim_response(session, claim, include_sources=False)


def _claim_response(
    session,
    claim: Claim | None,
    *,
    include_sources: bool = False,
) -> dict[str, object] | None:
    if claim is None:
        return None
    subject = session.get(Entity, claim.subject_entity_id)
    object_entity = session.get(Entity, claim.object_entity_id) if claim.object_entity_id else None
    subject_ref = _entity_ref(subject, claim.subject_entity_id)
    object_ref = (
        _entity_ref(object_entity, claim.object_entity_id)
        if object_entity is not None and claim.object_entity_id is not None
        else {"id": None, "name": claim.object_value or ""}
    )
    confidence = claim.confidence if claim.confidence is not None else claim.confidence_score
    evidence = _claim_evidence(session, claim.id)
    response = {
        "id": str(claim.id),
        "claim_id": str(claim.id),
        "predicate": claim.predicate,
        "confidence": confidence,
        "confidence_score": claim.confidence_score,
        "subject": subject_ref,
        "object": object_ref,
        "object_value": claim.object_value,
        "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
        "valid_to": claim.valid_to.isoformat() if claim.valid_to else None,
        "status": claim.status,
        "evidence_count": len(evidence),
        "statement": _claim_statement(subject_ref["name"], claim.predicate, object_ref["name"]),
        "evidence": evidence,
    }
    if include_sources:
        response["sources"] = _claim_sources_from_evidence(evidence)[:3]
    return response


def _entity_ref(entity: Entity | None, entity_id: uuid.UUID) -> dict[str, str]:
    name = entity.canonical_name if entity and entity.canonical_name else None
    return {
        "id": str(entity_id),
        "name": name or f"Unknown entity {str(entity_id)[:8]}",
    }


def _entity_record(session, entity_id: uuid.UUID | None) -> dict[str, object] | None:
    if entity_id is None:
        return None
    entity = session.get(Entity, entity_id)
    if entity is None:
        return {"id": str(entity_id), "name": f"Unknown entity {str(entity_id)[:8]}"}
    aliases = list(
        session.execute(
            select(EntityAlias.alias).where(EntityAlias.entity_id == entity_id)
        ).scalars()
    )
    return {
        "id": str(entity.id),
        "display_name": entity.canonical_name,
        "canonical_name": entity.canonical_name,
        "type": entity.kind,
        "superseded_by_entity_id": (
            str(entity.superseded_by_entity_id) if entity.superseded_by_entity_id else None
        ),
        "aliases": aliases,
    }


def _claim_statement(subject: str, predicate: str, object_value: object) -> str:
    return " ".join(
        part
        for part in [
            str(subject or "").strip(),
            str(predicate or "").replace("_", " ").strip(),
            str(object_value or "").strip(),
        ]
        if part
    )


def _claim_evidence(session, claim_id: uuid.UUID) -> list[dict[str, object]]:
    rows = list(
        session.execute(
            select(ClaimEvidence, Source, ClaimRaw, Chunk, Document, Fetch)
            .join(Source, Source.id == ClaimEvidence.source_id)
            .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
            .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .join(DocumentFetch, DocumentFetch.document_id == Document.id)
            .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(ClaimEvidence.claim_id == claim_id)
            .where(SourceRun.source_id == ClaimEvidence.source_id)
            .order_by(ClaimEvidence.added_at.desc(), Fetch.fetched_at.desc())
        ).all()
    )
    output = []
    seen: set[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = set()
    for evidence, source, raw, chunk, document, fetch in rows:
        key = (source.id, raw.id, document.id)
        if key in seen:
            continue
        seen.add(key)
        document_url = document.canonical_url or fetch.url
        snippet = _snippet(
            raw.raw_quote or chunk.text,
            chunk.text,
            max_chars=get_settings().snippet_max_chars,
        )
        output.append(
            {
                "source_id": str(source.id),
                "source_identifier": source.identifier,
                "source_name": source.display_name or source.identifier,
                "document_id": str(document.id),
                "document_title": document.title,
                "document_url": document_url,
                "url": fetch.url,
                "live_url": document_url,
                "fetched_at": fetch.fetched_at.isoformat(),
                "weight": evidence.weight,
                "raw_quote": snippet,
                "snippet": snippet,
                "added_at": evidence.added_at.isoformat(),
                "audit_verdict": None,
            }
        )
    return output


def _claim_sources_from_evidence(evidence: list[dict[str, object]]) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    seen_documents: set[str] = set()
    for row in evidence:
        document_id = str(row.get("document_id") or "")
        url = str(row.get("document_url") or row.get("url") or "")
        key = document_id or url
        if not key or key in seen_documents:
            continue
        seen_documents.add(key)
        sources.append(
            {
                "id": str(row.get("source_id") or ""),
                "name": str(row.get("source_name") or row.get("source_identifier") or "Source"),
                "url": url,
                "document_id": document_id,
                "title": row.get("document_title") or row.get("source_name") or url,
                "fetched_at": row.get("fetched_at"),
                "snippet": row.get("snippet") or row.get("raw_quote"),
            }
        )
    return sources


def _snippet(anchor: str | None, chunk_text: str, *, max_chars: int) -> str:
    text = " ".join((chunk_text or anchor or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    anchor_text = " ".join((anchor or "").split())
    index = text.casefold().find(anchor_text.casefold()) if anchor_text else -1
    if index < 0:
        return _sentence_trim(text, max_chars=max_chars)
    start = max(0, text.rfind(". ", 0, index) + 2)
    end = index + len(anchor_text)
    for _ in range(2):
        next_stop = text.find(". ", end)
        if next_stop < 0:
            end = len(text)
            break
        end = next_stop + 1
        if end - start >= max_chars:
            break
    return _sentence_trim(text[start:end].strip(), max_chars=max_chars)


def _sentence_trim(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    window = text[:max_chars].rstrip()
    stop = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if stop >= max_chars // 3:
        return window[: stop + 1].strip()
    return f"{window.rstrip(' ,.;:')}..."


async def _answer_from_graph(
    store: Store,
    question: str,
    *,
    max_results: int,
) -> tuple[str, list[dict[str, object]]]:
    settings, chunks, claims, citations = await _answer_materials(
        store, question, max_results=max_results
    )
    if not claims and not chunks:
        return (
            "No extracted graph evidence is available yet. Run ingestion and parse first.",
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


async def _answer_materials(
    store: Store,
    question: str,
    *,
    max_results: int,
) -> tuple[Settings, list[Chunk], list[dict[str, object]], list[dict[str, object]]]:
    settings = get_settings()
    question_variants = expand_class_year_synonyms(question)
    question_vectors = await embed_texts(question_variants)
    query_terms = _query_terms(question)
    with store.session() as session:
        lexical_chunks = _lexical_chunks(session, query_terms, limit=50)
        candidate_chunks = lexical_chunks or list(
            session.execute(select(Chunk).limit(1000)).scalars()
        )
        chunks = _rank_chunks(candidate_chunks, question_vectors, query_terms)[:8]
        claims = _claims_for_question(session, query_terms, max_results=max_results)
        claim_payloads = [
            payload for payload in (_claim_response(session, claim) for claim in claims) if payload
        ]
        citations = []
        seen_source_urls: set[str] = set()
        for claim in claims:
            claim_payload = next(
                (payload for payload in claim_payloads if payload["id"] == str(claim.id)),
                None,
            )
            for evidence in _claim_evidence(session, claim.id):
                document_id = str(evidence["document_id"])
                source_url = str(evidence.get("document_url") or document_id)
                if source_url in seen_source_urls:
                    continue
                seen_source_urls.add(source_url)
                citations.append(
                    {
                        "claim_id": str(claim.id),
                        "claim": claim_payload,
                        "source_id": evidence["source_id"],
                        "source_name": evidence["source_name"],
                        "title": evidence["document_title"] or evidence["source_name"],
                        "document_id": document_id,
                        "document_url": evidence["document_url"],
                        "fetched_at": evidence["fetched_at"],
                        "quote": evidence["snippet"] or evidence["raw_quote"],
                        "evidence": [evidence],
                    }
                )
                if len(citations) >= max_results:
                    break
            if len(citations) >= max_results:
                break
        for chunk in chunks:
            citation = _chunk_citation(session, chunk, query_terms)
            if citation is None:
                continue
            source_url = str(citation.get("document_url") or citation.get("document_id"))
            if source_url in seen_source_urls:
                continue
            seen_source_urls.add(source_url)
            citations.append(citation)
            if len(citations) >= max_results:
                break
    return settings, chunks, claim_payloads, citations


async def _llm_answer(
    *,
    question: str,
    chunks: list[Chunk],
    claims: list[dict[str, object]],
    citations: list[dict[str, object]],
    model: str,
    api_key: str,
) -> tuple[str, list[dict[str, object]]]:
    parts = [
        token
        async for token in _llm_answer_tokens(
            question=question,
            chunks=chunks,
            claims=claims,
            model=model,
            api_key=api_key,
        )
    ]
    content = "".join(parts)
    return content.strip() or "No answer could be generated from the available evidence.", citations


async def _llm_answer_tokens(
    *,
    question: str,
    chunks: list[Chunk],
    claims: list[dict[str, object]],
    model: str,
    api_key: str,
) -> AsyncIterator[str]:
    chunk_context = "\n\n".join(
        f"[chunk {index + 1}] {chunk.text}" for index, chunk in enumerate(chunks)
    )
    claim_context = "\n".join(
        (
            f"- {payload['statement']} "
            f"(claim_id={payload['id']}, confidence={float(payload.get('confidence') or 0):.2f})"
        )
        for payload in claims
    )
    client = AsyncOpenAI(api_key=api_key)
    stream = await client.chat.completions.create(
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
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        token = chunk.choices[0].delta.content
        if token:
            yield token


def _query_terms(question: str) -> list[str]:
    stopwords = {
        "about",
        "from",
        "have",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "worked",
        "work",
        "with",
    }
    terms = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", question):
        folded = token.casefold()
        if len(folded) < 4 or folded in stopwords:
            continue
        if folded not in terms:
            terms.append(folded)
    return terms


def _lexical_chunks(session, query_terms: list[str], *, limit: int) -> list[Chunk]:
    if not query_terms:
        return []
    conditions = [Chunk.text.ilike(f"%{term}%") for term in query_terms]
    return list(
        session.execute(
            select(Chunk).where(or_(*conditions)).order_by(Chunk.created_at.desc()).limit(limit)
        ).scalars()
    )


def _claims_for_question(session, query_terms: list[str], *, max_results: int) -> list[Claim]:
    query = select(Claim).order_by(Claim.confidence_score.desc()).limit(max_results)
    if not query_terms:
        return list(session.execute(query).scalars())
    subject = Entity.__table__.alias("ask_claim_subject")
    obj = Entity.__table__.alias("ask_claim_object")
    conditions = []
    for term in query_terms:
        pattern = f"%{term}%"
        conditions.extend(
            [
                subject.c.canonical_name.ilike(pattern),
                obj.c.canonical_name.ilike(pattern),
                Claim.object_value.ilike(pattern),
                ClaimRaw.raw_quote.ilike(pattern),
                Chunk.text.ilike(pattern),
            ]
        )
    return list(
        session.execute(
            select(Claim)
            .outerjoin(subject, subject.c.id == Claim.subject_entity_id)
            .outerjoin(obj, obj.c.id == Claim.object_entity_id)
            .outerjoin(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
            .outerjoin(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
            .outerjoin(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .where(or_(*conditions))
            .order_by(Claim.confidence_score.desc())
            .limit(max_results)
        )
        .unique()
        .scalars()
    )


def _chunk_citation(
    session,
    chunk: Chunk,
    query_terms: list[str],
) -> dict[str, object] | None:
    row = session.execute(
        select(Document, Source, Fetch)
        .join(DocumentFetch, DocumentFetch.document_id == Document.id)
        .join(Fetch, Fetch.id == DocumentFetch.fetch_id)
        .join(SourceRun, SourceRun.id == Fetch.source_run_id)
        .join(Source, Source.id == SourceRun.source_id)
        .where(Document.id == chunk.document_id)
        .order_by(Fetch.fetched_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    document, source, fetch = row
    document_url = document.canonical_url or fetch.url
    anchor = next((term for term in query_terms if term in chunk.text.casefold()), None)
    snippet = _snippet(anchor, chunk.text, max_chars=get_settings().snippet_max_chars)
    return {
        "claim_id": None,
        "claim": None,
        "source_id": str(source.id),
        "source_name": source.display_name or source.identifier,
        "title": document.title or document_url,
        "document_id": str(document.id),
        "document_url": document_url,
        "fetched_at": fetch.fetched_at.isoformat(),
        "quote": snippet,
        "evidence": [
            {
                "source_id": str(source.id),
                "source_identifier": source.identifier,
                "source_name": source.display_name or source.identifier,
                "document_id": str(document.id),
                "document_title": document.title,
                "document_url": document_url,
                "url": fetch.url,
                "live_url": document_url,
                "fetched_at": fetch.fetched_at.isoformat(),
                "raw_quote": chunk.text,
                "snippet": snippet,
            }
        ],
    }


def _rank_chunks(
    chunks: list[Chunk],
    question_vectors: list[list[float]],
    query_terms: list[str] | None = None,
) -> list[Chunk]:
    if not chunks:
        return []
    query_terms = query_terms or []

    def score(chunk: Chunk) -> tuple[int, float]:
        text = str(getattr(chunk, "text", "") or "").casefold()
        lexical = sum(1 for term in query_terms if term in text)
        vector_score = 0.0
        if question_vectors and any(any(vector) for vector in question_vectors):
            vector_score = max(
                cosine(question_vector, vector_values(chunk.embedding))
                for question_vector in question_vectors
            )
        return lexical, vector_score

    return sorted(
        chunks,
        key=score,
        reverse=True,
    )


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")
