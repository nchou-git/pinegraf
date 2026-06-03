from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import HTTPException
from openai import AsyncOpenAI
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.orm import aliased

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
    source_coverage_many,
)
from backend.db.store import Store, utc_now
from backend.extraction.extractor import is_structurally_valid_name
from backend.resolution.embedder import embed_texts
from backend.util.vector import cosine, vector_values

ASK_CACHE_SECONDS = 3600
ASK_CACHE_MAX = 100
ACTIVE_SOURCE_RUN_STATUSES = {"queued", "running"}
STOPPED_SOURCE_RUN_STATUS = "stopped"
_ASK_CACHE: OrderedDict[str, tuple[float, str, list[dict[str, object]]]] = OrderedDict()
LOGGER = logging.getLogger("uvicorn.error")


class ActiveSourceRunError(RuntimeError):
    def __init__(self, run_id: uuid.UUID, status: str) -> None:
        super().__init__("source has an active run")
        self.run_id = run_id
        self.status = status


def _source_run_action_kind(run: SourceRun) -> str:
    if run.kind == "parse":
        return "parse"
    return "crawl"


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


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
                .order_by(Entity.canonical_name.asc())
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
                .order_by(EntityNeighborhood.evidence_count.desc())
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
                query.order_by(Claim.first_seen_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars()
        )
        return {
            "claims": _claim_list_responses(session, rows),
            "total": total,
            "page": page,
            "page_size": page_size,
            "filters_applied": {
                "predicate": predicate or None,
                "subject_entity_id": str(subject_entity_id) if subject_entity_id else None,
                "object_entity_id": str(object_entity_id) if object_entity_id else None,
                "source_id": str(source_id) if source_id else None,
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
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[bytes]:
    conversation_history = _clean_ask_history(history)
    key = json.dumps(
        {
            "question": " ".join(question.casefold().split()),
            "history": conversation_history,
        },
        sort_keys=True,
    )
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
        answer = (
            "The current graph doesn't contain information about that topic. "
            "The demo graph indexes a curated subset of Tuck's alumni network; try exploring "
            "alumni, companies, roles, or sources already present in the directory."
        )
        yield _sse({"kind": "token", "text": answer})
    elif settings.openai_api_key:
        parts: list[str] = []
        async for token in _llm_answer_tokens(
            question=question,
            chunks=chunks,
            claims=claims,
            history=conversation_history,
            model=settings.extraction_model,
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
    started = time.perf_counter()
    source_query_ms = 0.0
    recent_runs_ms = 0.0
    active_payload_ms = 0.0
    paused_payload_ms = 0.0
    stat_queries_ms = 0.0
    response_build_ms = 0.0
    included_count = 0
    with store.session() as session:
        section_started = time.perf_counter()
        sources = list(
            session.execute(
                select(Source).order_by(
                    Source.created_at.asc().nullslast(), Source.identifier.asc()
                )
            ).scalars()
        )
        source_query_ms = _elapsed_ms(section_started)
        included_sources = [
            source for source in sources if include_archived or not _source_is_archived(source)
        ]
        included_count = len(included_sources)

        section_started = time.perf_counter()
        source_ids = [source.id for source in included_sources]
        runs_by_source = _recent_runs_by_source(session, source_ids, limit=5)
        recent_runs_ms = _elapsed_ms(section_started)

        section_started = time.perf_counter()
        coverage_by_source = source_coverage_many(session, source_ids)
        stat_queries_ms = _elapsed_ms(section_started)

        output = []
        loop_started = time.perf_counter()
        for source in included_sources:
            runs = runs_by_source.get(source.id, [])
            last_run = runs[0] if runs else None
            active_run = next(
                (run for run in runs if run.status in ACTIVE_SOURCE_RUN_STATUSES),
                None,
            )
            section_started = time.perf_counter()
            active_runs = _active_runs_payload(runs)
            active_payload_ms += _elapsed_ms(section_started)
            section_started = time.perf_counter()
            paused_runs = _paused_runs_payload(runs)
            paused_payload_ms += _elapsed_ms(section_started)
            coverage = dict(coverage_by_source[source.id])
            if active_runs.get("parse"):
                coverage["pending_parse_count"] = 0
            section_started = time.perf_counter()
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
            response_build_ms += _elapsed_ms(section_started)
        per_source_loop_ms = _elapsed_ms(loop_started)
        total_ms = _elapsed_ms(started)
        LOGGER.info(
            "api.sources timing include_archived=%s sources_total=%s sources_returned=%s "
            "total_ms=%.1f source_query_ms=%.1f per_source_loop_ms=%.1f "
            "recent_runs_ms=%.1f stat_queries_ms=%.1f active_runs_payload_ms=%.1f "
            "paused_runs_payload_ms=%.1f response_build_ms=%.1f",
            include_archived,
            len(sources),
            included_count,
            total_ms,
            source_query_ms,
            per_source_loop_ms,
            recent_runs_ms,
            stat_queries_ms,
            active_payload_ms,
            paused_payload_ms,
            response_build_ms,
        )
        return output


def _recent_runs_by_source(
    session,
    source_ids: list[uuid.UUID],
    *,
    limit: int,
) -> dict[uuid.UUID, list[SourceRun]]:
    if not source_ids:
        return {}
    ranked = (
        select(
            SourceRun.id.label("id"),
            func.row_number()
            .over(
                partition_by=SourceRun.source_id,
                order_by=SourceRun.started_at.desc(),
            )
            .label("rank"),
        )
        .where(SourceRun.source_id.in_(source_ids))
        .subquery()
    )
    run_alias = aliased(SourceRun)
    rows = list(
        session.execute(
            select(run_alias)
            .join(ranked, run_alias.id == ranked.c.id)
            .where(ranked.c.rank <= limit)
            .order_by(run_alias.source_id.asc(), run_alias.started_at.desc())
        ).scalars()
    )
    grouped = {source_id: [] for source_id in source_ids}
    for run in rows:
        grouped.setdefault(run.source_id, []).append(run)
    return grouped


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


def raw_document_detail(store: Store, document_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        document = session.get(Document, document_id)
        if document is None:
            return None
        chunks = list(
            session.execute(
                select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal.asc())
            ).scalars()
        )
        return {
            "id": str(document.id),
            "document_id": str(document.id),
            "canonical_url": document.canonical_url,
            "title": document.title,
            "language": document.language,
            "word_count": document.word_count,
            "valid_from": document.valid_from.isoformat() if document.valid_from else None,
            "cleaned_text": document.cleaned_text,
            "chunk_count": len(chunks),
            "chunks": [
                {
                    "chunk_id": str(chunk.id),
                    "ordinal": chunk.ordinal,
                    "char_count": len(chunk.text or ""),
                }
                for chunk in chunks
            ],
        }


def raw_chunk_detail(store: Store, chunk_id: uuid.UUID) -> dict[str, object] | None:
    with store.session() as session:
        row = session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id == chunk_id)
        ).one_or_none()
        if row is None:
            return None
        chunk, document = row
        raw_rows = list(
            session.execute(
                select(ClaimRaw)
                .where(ClaimRaw.chunk_id == chunk.id)
                .order_by(ClaimRaw.extracted_at.desc(), ClaimRaw.id)
            ).scalars()
        )
        return {
            "id": str(chunk.id),
            "chunk_id": str(chunk.id),
            "text": chunk.text,
            "ordinal": chunk.ordinal,
            "document_id": str(document.id),
            "document": {
                "id": str(document.id),
                "canonical_url": document.canonical_url,
                "title": document.title,
            },
            "claim_raw": [
                {
                    "id": str(raw.id),
                    "subject_text": raw.subject_text,
                    "predicate": raw.predicate,
                    "object_text": raw.object_text,
                    "raw_quote": raw.raw_quote,
                }
                for raw in raw_rows
            ],
        }


def list_raw_claims(
    store: Store,
    *,
    q: str = "",
    predicate: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    page = max(1, page)
    page_size = min(max(page_size, 1), 200)
    q = q.strip()
    predicate = predicate.strip()
    filters = []
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(ClaimRaw.subject_text.ilike(pattern), ClaimRaw.object_text.ilike(pattern))
        )
    if predicate:
        filters.append(ClaimRaw.predicate == predicate)

    promoted = exists(
        select(1).select_from(ClaimEvidence).where(ClaimEvidence.claim_raw_id == ClaimRaw.id)
    )
    with store.session() as session:
        count_query = (
            select(func.count())
            .select_from(ClaimRaw)
            .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
        )
        rows_query = (
            select(ClaimRaw, Chunk, Document, promoted.label("promoted_to_claim"))
            .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .order_by(ClaimRaw.extracted_at.desc(), ClaimRaw.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            count_query = count_query.where(*filters)
            rows_query = rows_query.where(*filters)
        total = session.execute(count_query).scalar_one()
        rows = list(session.execute(rows_query).all())

    results = []
    for raw, chunk, document, promoted_to_claim in rows:
        promoted_bool = bool(promoted_to_claim)
        results.append(
            {
                "id": str(raw.id),
                "claim_raw_id": str(raw.id),
                "chunk_id": str(chunk.id),
                "document_id": str(document.id),
                "canonical_url": document.canonical_url,
                "document": {
                    "id": str(document.id),
                    "canonical_url": document.canonical_url,
                    "title": document.title,
                },
                "subject_text": raw.subject_text,
                "predicate": raw.predicate,
                "object_text": raw.object_text,
                "raw_quote": raw.raw_quote,
                "promoted_to_claim": promoted_bool,
                "promotion_status": "promoted" if promoted_bool else "filtered_out",
                "extracted_at": raw.extracted_at.isoformat(),
            }
        )
    return {
        "claim_raw": results,
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "filters_applied": {"q": q, "predicate": predicate},
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
    filter: str = "pending",
    low_signal_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, object]:
    if page is not None or page_size is not None:
        page = max(page or 1, 1)
        limit = page_size or limit
        offset = (page - 1) * limit
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    with store.session() as session:
        _auto_split_invalid_identity_candidates(session)
        base = select(EntityDisambiguationCandidate)
        if filter == "pending":
            base = base.where(EntityDisambiguationCandidate.review_decision.is_(None))
        elif filter != "all":
            base = base.where(EntityDisambiguationCandidate.review_decision == filter)
        if low_signal_only:
            base = base.join(
                EntityMention,
                EntityMention.id == EntityDisambiguationCandidate.mention_id,
            )
            base = base.join(Entity, Entity.id == EntityMention.entity_id).where(
                Entity.needs_human_disambiguation.is_(True)
            )
        total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        rows = list(
            session.execute(
                base.order_by(
                    EntityDisambiguationCandidate.name_similarity_score.desc().nullslast(),
                    EntityDisambiguationCandidate.created_at.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        return {
            "total": total,
            "page": (offset // limit) + 1,
            "page_size": limit,
            "limit": limit,
            "offset": offset,
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
    if decision == "confirm":
        decision = "split"
    if decision not in {"merge", "split", "defer"}:
        raise HTTPException(status_code=400, detail="decision must be merge, split, or defer")
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
            result = _merge_identity_candidate(session, row, reviewer or audit_actor or "admin")
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


def add_entity_alias(
    store: Store,
    *,
    entity_id: uuid.UUID,
    alias: str,
    reviewer: str,
    audit_actor: str | None,
    audit_request_ip: str | None,
) -> dict[str, object]:
    alias = alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias is required")
    with store.session() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        existing = session.execute(
            select(EntityAlias).where(
                EntityAlias.entity_id == entity_id,
                func.lower(EntityAlias.alias) == alias.casefold(),
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                EntityAlias(
                    entity_id=entity_id,
                    alias=alias,
                    confidence=1.0,
                    source=f"human:{reviewer}",
                )
            )
        _add_audit_row(
            session,
            action="entity.alias_added",
            target_table="entities",
            target_id=entity_id,
            actor=audit_actor,
            request_ip=audit_request_ip,
            payload={"alias": alias, "reviewer": reviewer},
        )
        session.commit()
        return {"status": "ok", "entity": _entity_record(session, entity_id)}


def verify_entity(
    store: Store,
    *,
    entity_id: uuid.UUID,
    reviewer: str,
    audit_actor: str | None,
    audit_request_ip: str | None,
) -> dict[str, object]:
    with store.session() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        entity.verified_by = reviewer
        entity.verified_at = utc_now()
        _add_audit_row(
            session,
            action="entity.verified",
            target_table="entities",
            target_id=entity_id,
            actor=audit_actor,
            request_ip=audit_request_ip,
            payload={"reviewer": reviewer},
        )
        session.commit()
        return {"status": "ok", "entity": _entity_record(session, entity_id)}


def _auto_split_invalid_identity_candidates(session) -> None:
    rows = list(
        session.execute(
            select(EntityDisambiguationCandidate, Entity)
            .join(Entity, Entity.id == EntityDisambiguationCandidate.candidate_entity_id)
            .where(EntityDisambiguationCandidate.review_decision.is_(None))
        ).all()
    )
    changed = False
    for row, candidate in rows:
        if candidate.kind != "person" or is_structurally_valid_name(
            candidate.canonical_name, "person"
        ):
            continue
        row.review_decision = "split"
        row.reviewed_by = "auto"
        row.reviewed_at = utc_now()
        _add_audit_row(
            session,
            action="identity_review.auto_split",
            target_table="entity_disambiguation_candidates",
            target_id=row.id,
            actor="system",
            request_ip=None,
            payload={
                "reason": "auto-split: name pattern is not a structurally valid person name",
                "candidate_entity_id": str(candidate.id),
                "candidate_name": candidate.canonical_name,
            },
        )
        changed = True
    if changed:
        session.flush()


def _identity_review_payload(
    session,
    row: EntityDisambiguationCandidate,
) -> dict[str, object]:
    mention = session.get(EntityMention, row.mention_id) if row.mention_id else None
    candidate = session.get(Entity, row.candidate_entity_id)
    source_entity = session.get(Entity, mention.entity_id) if mention else None
    if source_entity is None and candidate is not None and candidate.needs_human_disambiguation:
        source_entity = candidate
    chunk = session.get(Chunk, row.context_chunk_id) if row.context_chunk_id else None
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat(),
        "llm_decision": row.llm_decision,
        "llm_reasoning": row.llm_reasoning,
        "name_similarity_score": row.name_similarity_score,
        "review_decision": row.review_decision,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "mention_text": row.mention_text or (mention.mention_text if mention else None),
        "context_chunk_id": str(row.context_chunk_id) if row.context_chunk_id else None,
        "context_chunk_text": chunk.text if chunk is not None else None,
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
        "candidate_top_claims": _identity_review_top_claims(session, row.candidate_entity_id),
    }


def _identity_review_top_claims(session, entity_id: uuid.UUID) -> list[dict[str, object]]:
    rows = session.execute(
        select(Claim, Entity, Source, Document)
        .outerjoin(Entity, Entity.id == Claim.object_entity_id)
        .outerjoin(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .outerjoin(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
        .outerjoin(Chunk, Chunk.id == ClaimRaw.chunk_id)
        .outerjoin(Document, Document.id == Chunk.document_id)
        .outerjoin(Source, Source.id == ClaimEvidence.source_id)
        .where(or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id))
        .order_by(Claim.last_corroborated_at.desc())
        .limit(5)
    ).all()
    output = []
    for claim, object_entity, source, document in rows:
        output.append(
            {
                "id": str(claim.id),
                "predicate": claim.predicate,
                "object": object_entity.canonical_name if object_entity else claim.object_value,
                "source_url": document.canonical_url if document else None,
                "source_name": source.display_name or source.identifier if source else None,
            }
        )
    return output


def _merge_identity_candidate(
    session,
    row: EntityDisambiguationCandidate,
    reviewer: str = "admin",
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
    source.status = "merged"
    source.merged_into_entity_id = target_id
    source.updated_at = utc_now()
    if row.mention_text:
        _add_alias_if_missing(session, target, row.mention_text, f"human:{reviewer}")
    target.verified_by = reviewer
    target.verified_at = utc_now()
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


def _add_alias_if_missing(session, entity: Entity, alias_text: str, source: str) -> None:
    key = _alias_key(alias_text)
    if not key or key == _alias_key(entity.canonical_name):
        return
    existing_keys = {
        _alias_key(alias)
        for alias in session.execute(
            select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
        ).scalars()
    }
    if key in existing_keys:
        return
    session.add(
        EntityAlias(
            entity_id=entity.id,
            alias=alias_text.strip(),
            confidence=1.0,
            source=source,
        )
    )


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
            .order_by(Claim.first_seen_at.desc())
        ).scalars()
    )
    grouped: dict[str, list[dict[str, object]]] = {}
    for claim in claims:
        grouped.setdefault(claim.predicate, []).append(
            {
                "claim_id": str(claim.id),
                "object_value": claim.object_value,
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
    claims = list(session.execute(query.order_by(Claim.first_seen_at.desc()).limit(10)).scalars())
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
    evidence = _claim_evidence(session, claim.id)
    response = {
        "id": str(claim.id),
        "claim_id": str(claim.id),
        "predicate": claim.predicate,
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


def _claim_list_responses(session, claims: list[Claim]) -> list[dict[str, object]]:
    if not claims:
        return []
    claim_ids = [claim.id for claim in claims]
    entity_ids = {
        entity_id
        for claim in claims
        for entity_id in (claim.subject_entity_id, claim.object_entity_id)
        if entity_id is not None
    }
    entities = {
        entity.id: entity
        for entity in session.execute(select(Entity).where(Entity.id.in_(entity_ids))).scalars()
    }
    evidence_counts = {
        claim_id: int(count or 0)
        for claim_id, count in session.execute(
            select(ClaimEvidence.claim_id, func.count(func.distinct(ClaimEvidence.claim_raw_id)))
            .where(ClaimEvidence.claim_id.in_(claim_ids))
            .group_by(ClaimEvidence.claim_id)
        ).all()
    }
    sources_by_claim = _claim_sources_for_claims(session, claim_ids)
    output = []
    for claim in claims:
        subject_ref = _entity_ref(entities.get(claim.subject_entity_id), claim.subject_entity_id)
        object_entity = entities.get(claim.object_entity_id) if claim.object_entity_id else None
        object_ref = (
            _entity_ref(object_entity, claim.object_entity_id)
            if object_entity is not None and claim.object_entity_id is not None
            else {"id": None, "name": claim.object_value or ""}
        )
        output.append(
            {
                "id": str(claim.id),
                "claim_id": str(claim.id),
                "predicate": claim.predicate,
                "subject": subject_ref,
                "object": object_ref,
                "object_value": claim.object_value,
                "valid_from": claim.valid_from.isoformat() if claim.valid_from else None,
                "valid_to": claim.valid_to.isoformat() if claim.valid_to else None,
                "status": claim.status,
                "evidence_count": evidence_counts.get(claim.id, 0),
                "statement": _claim_statement(
                    subject_ref["name"],
                    claim.predicate,
                    object_ref["name"],
                ),
                "evidence": [],
                "sources": sources_by_claim.get(claim.id, []),
            }
        )
    return output


def _claim_sources_for_claims(
    session,
    claim_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[dict[str, object]]]:
    rows = list(
        session.execute(
            select(ClaimEvidence.claim_id, Source, ClaimRaw, Chunk, Document, Fetch)
            .join(Source, Source.id == ClaimEvidence.source_id)
            .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
            .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
            .join(Document, Document.id == Chunk.document_id)
            .join(Fetch, Fetch.id == Document.first_seen_fetch_id)
            .where(ClaimEvidence.claim_id.in_(claim_ids))
            .order_by(ClaimEvidence.claim_id.asc(), ClaimEvidence.added_at.desc())
        ).all()
    )
    output: dict[uuid.UUID, list[dict[str, object]]] = {claim_id: [] for claim_id in claim_ids}
    seen_documents: dict[uuid.UUID, set[str]] = {claim_id: set() for claim_id in claim_ids}
    max_chars = get_settings().snippet_max_chars
    for claim_id, source, raw, chunk, document, fetch in rows:
        if len(output.setdefault(claim_id, [])) >= 3:
            continue
        document_url = document.canonical_url or fetch.url
        document_id = str(document.id)
        key = document_id or document_url
        if key in seen_documents.setdefault(claim_id, set()):
            continue
        seen_documents[claim_id].add(key)
        snippet = _snippet(raw.raw_quote or chunk.text, chunk.text, max_chars=max_chars)
        output[claim_id].append(
            {
                "id": str(source.id),
                "name": source.display_name or source.identifier,
                "url": document_url,
                "document_id": document_id,
                "title": document.title or source.display_name or document_url,
                "fetched_at": fetch.fetched_at.isoformat(),
                "snippet": snippet,
            }
        )
    return output


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
        "kind": entity.kind,
        "verified_by": entity.verified_by,
        "verified_at": entity.verified_at.isoformat() if entity.verified_at else None,
        "needs_human_disambiguation": entity.needs_human_disambiguation,
        "status": entity.status,
        "merged_into_entity_id": (
            str(entity.merged_into_entity_id) if entity.merged_into_entity_id else None
        ),
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
    history: list[dict[str, str]] | None = None,
    model: str,
    api_key: str,
) -> tuple[str, list[dict[str, object]]]:
    parts = [
        token
        async for token in _llm_answer_tokens(
            question=question,
            chunks=chunks,
            claims=claims,
            history=_clean_ask_history(history),
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
    history: list[dict[str, str]] | None = None,
    model: str,
    api_key: str,
) -> AsyncIterator[str]:
    chunk_context = "\n\n".join(
        f"[chunk {index + 1}] {chunk.text}" for index, chunk in enumerate(chunks)
    )
    claim_context = "\n".join(
        f"- {payload['statement']} (claim_id={payload['id']})" for payload in claims
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Answer questions using only the supplied Pinegraf chunks and graph claims. "
                "Use the conversation history only to resolve follow-up references, pronouns, "
                "and omitted subjects. Cite source URLs inline as markdown links when possible. "
                "If the evidence is insufficient, say so plainly."
            ),
        }
    ]
    for turn in _clean_ask_history(history):
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["answer"]})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Graph claims:\n{claim_context or 'none'}\n\n"
                f"Retrieved chunks:\n{chunk_context or 'none'}"
            ),
        }
    )
    client = AsyncOpenAI(api_key=api_key)
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        stream=True,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        token = chunk.choices[0].delta.content
        if token:
            yield token


def _clean_ask_history(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for turn in (history or [])[-6:]:
        question = str(turn.get("question") or "").strip()
        answer = str(turn.get("answer") or "").strip()
        if not question or not answer:
            continue
        cleaned.append(
            {
                "question": question[:4000],
                "answer": answer[:12000],
            }
        )
    return cleaned


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
    query = select(Claim).order_by(Claim.first_seen_at.desc()).limit(max_results)
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
            .order_by(Claim.first_seen_at.desc())
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
