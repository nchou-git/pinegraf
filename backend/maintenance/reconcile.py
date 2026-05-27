from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, exists, func, select

from backend.db.models import AuditLog, Document, DocumentFetch, Fetch, Source, SourceRun
from backend.db.stats_queries import pages_fetched, pending_parse_count, urls_known
from backend.db.store import Store, utc_now


def reconcile_all_sources(store: Store) -> dict[str, int]:
    summary = {
        "sources": 0,
        "stale_runs_failed": 0,
        "dangling_documentfetch_deleted": 0,
        "orphan_documents_flagged": 0,
        "broken_body_chains": 0,
        "stats_counters_corrected": 0,
        "pending_parse_sources": 0,
    }
    with store.session() as session:
        source_ids = list(session.execute(select(Source.id)).scalars())
    for source_id in source_ids:
        result = reconcile_source(store, source_id)
        summary["sources"] += 1
        for key, value in result.items():
            summary[key] = summary.get(key, 0) + value
    return summary


def reconcile_source(store: Store, source_id: uuid.UUID) -> dict[str, int]:
    result = {
        "stale_runs_failed": 0,
        "dangling_documentfetch_deleted": 0,
        "orphan_documents_flagged": 0,
        "broken_body_chains": 0,
        "stats_counters_corrected": 0,
        "pending_parse_sources": 0,
    }
    cutoff = utc_now() - timedelta(minutes=30)
    orphan_cutoff = utc_now() - timedelta(days=7)
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return result

        stale_runs = list(
            session.execute(
                select(SourceRun)
                .where(SourceRun.source_id == source_id)
                .where(SourceRun.status == "running")
                .where(func.coalesce(SourceRun.stats_updated_at, SourceRun.started_at) < cutoff)
            ).scalars()
        )
        for run in stale_runs:
            run.status = "failed"
            run.finished_at = utc_now()
            run.error_message = "stalled - no progress for 30+ minutes"
            _audit(
                session,
                "run.auto_failed_stale",
                "source_runs",
                run.id,
                {"source_id": str(source_id), "stats_updated_at": _iso(run.stats_updated_at)},
            )
        result["stale_runs_failed"] = len(stale_runs)

        pending = pending_parse_count(session, source_id)
        if pending:
            result["pending_parse_sources"] = 1
            _audit(
                session,
                "maintenance.pending_parse_visible",
                "sources",
                source_id,
                {"pending_parse_count": pending},
            )

        dangling = session.execute(
            select(DocumentFetch)
            .outerjoin(Document, Document.id == DocumentFetch.document_id)
            .outerjoin(Fetch, Fetch.id == DocumentFetch.fetch_id)
            .where((Document.id.is_(None)) | (Fetch.id.is_(None)))
        ).all()
        if dangling:
            deleted = session.execute(
                delete(DocumentFetch)
                .where(
                    (~exists().where(Document.id == DocumentFetch.document_id))
                    | (~exists().where(Fetch.id == DocumentFetch.fetch_id))
                )
                .execution_options(synchronize_session=False)
            )
            result["dangling_documentfetch_deleted"] = int(deleted.rowcount or 0)
            _audit(
                session,
                "maintenance.cleanup_dangling_documentfetch",
                "sources",
                source_id,
                {"count": result["dangling_documentfetch_deleted"]},
            )

        orphan_documents = list(
            session.execute(
                select(Document)
                .where(Document.created_at < orphan_cutoff)
                .where(~exists().where(DocumentFetch.document_id == Document.id))
            ).scalars()
        )
        for document in orphan_documents:
            result["orphan_documents_flagged"] += 1
            _audit(
                session,
                "maintenance.orphan_document_found",
                "documents",
                document.id,
                {"canonical_url": document.canonical_url},
            )

        broken_chains = _broken_body_chains(session, source_id)
        for fetch_id in broken_chains:
            result["broken_body_chains"] += 1
            _audit(
                session,
                "maintenance.broken_body_chain",
                "fetches",
                fetch_id,
                {"source_id": str(source_id)},
            )

        canonical_pages = pages_fetched(session, source_id)
        canonical_known = urls_known(session, source_id)
        old_pages = int(source.pages_fetched_total or 0)
        old_known = int(source.urls_known_total or 0)
        if old_pages != canonical_pages or old_known != canonical_known:
            source.pages_fetched_total = canonical_pages
            source.urls_known_total = max(canonical_known, canonical_pages)
            result["stats_counters_corrected"] = 1
            _audit(
                session,
                "maintenance.stats_counter_corrected",
                "sources",
                source_id,
                {
                    "pages_fetched_total": {"old": old_pages, "new": canonical_pages},
                    "urls_known_total": {"old": old_known, "new": canonical_known},
                },
            )
        session.commit()
    return result


def _broken_body_chains(session, source_id: uuid.UUID) -> list[uuid.UUID]:
    fetch_ids = list(
        session.execute(
            select(Fetch.id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
            .where(Fetch.body_unchanged_since.is_not(None))
        ).scalars()
    )
    return [
        fetch_id for fetch_id in fetch_ids if not _chain_resolves(session, fetch_id, seen=set())
    ]


def _chain_resolves(session, fetch_id: uuid.UUID, *, seen: set[uuid.UUID]) -> bool:
    if fetch_id in seen:
        return False
    seen.add(fetch_id)
    fetch = session.get(Fetch, fetch_id)
    if fetch is None:
        return False
    if fetch.body_bytes is not None:
        return True
    if fetch.body_unchanged_since is None:
        return False
    return _chain_resolves(session, fetch.body_unchanged_since, seen=seen)


def _audit(session, action: str, target_table: str, target_id: uuid.UUID, payload: dict) -> None:
    session.add(
        AuditLog(
            action=action,
            target_table=target_table,
            target_id=str(target_id),
            actor="system",
            payload=payload,
        )
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None
