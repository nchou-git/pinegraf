from __future__ import annotations

import uuid

from sqlalchemy import func, select

from backend.db.models import Document, DocumentFetch, Fetch, Source, SourceRun
from backend.db.stats_queries import documents_for_source, pages_fetched, urls_known
from backend.db.store import Store


def verify_source_integrity(store: Store, source_id: uuid.UUID) -> dict[str, object]:
    with store.session() as session:
        source = session.get(Source, source_id)
        if source is None:
            return {"ok": False, "error": "source not found"}
        source_fetches = (
            select(Fetch.id)
            .join(SourceRun, SourceRun.id == Fetch.source_run_id)
            .where(SourceRun.source_id == source_id)
        )
        fetches_total = _count(session, source_fetches)
        fetches_with_body = _count(
            session,
            source_fetches.where(
                Fetch.http_status.between(200, 299),
                Fetch.body_bytes.is_not(None),
            ),
        )
        unchanged_ids = list(
            session.execute(
                source_fetches.where(
                    Fetch.http_status.between(200, 299),
                    Fetch.body_unchanged_since.is_not(None),
                )
            ).scalars()
        )
        chain_broken_ids = [
            fetch_id
            for fetch_id in unchanged_ids
            if not _body_chain_resolves(session, fetch_id, seen=set())
        ]
        fetches_with_documentfetch_link = int(
            session.execute(
                select(func.count(func.distinct(DocumentFetch.fetch_id))).where(
                    DocumentFetch.fetch_id.in_(source_fetches)
                )
            ).scalar_one()
        )
        missing_rows = list(
            session.execute(
                select(Fetch.url)
                .where(Fetch.id.in_(source_fetches))
                .outerjoin(DocumentFetch, DocumentFetch.fetch_id == Fetch.id)
                .where(Fetch.http_status.between(200, 299))
                .where(Fetch.body_bytes.is_not(None))
                .where(Fetch.body_unchanged_since.is_(None))
                .where(Fetch.parse_skip_reason.is_(None))
                .where(DocumentFetch.fetch_id.is_(None))
                .order_by(Fetch.fetched_at.asc())
                .limit(50)
            ).scalars()
        )
        fetches_missing_documentfetch_link = int(
            session.execute(
                select(func.count())
                .select_from(Fetch)
                .where(Fetch.id.in_(source_fetches))
                .outerjoin(DocumentFetch, DocumentFetch.fetch_id == Fetch.id)
                .where(Fetch.http_status.between(200, 299))
                .where(Fetch.body_bytes.is_not(None))
                .where(Fetch.body_unchanged_since.is_(None))
                .where(Fetch.parse_skip_reason.is_(None))
                .where(DocumentFetch.fetch_id.is_(None))
            ).scalar_one()
        )
        documentfetch_dangling = int(
            session.execute(
                select(func.count())
                .select_from(DocumentFetch)
                .outerjoin(Document, Document.id == DocumentFetch.document_id)
                .outerjoin(Fetch, Fetch.id == DocumentFetch.fetch_id)
                .where((Document.id.is_(None)) | (Fetch.id.is_(None)))
            ).scalar_one()
        )
        documents_orphan = int(
            session.execute(
                select(func.count(func.distinct(DocumentFetch.document_id)))
                .select_from(DocumentFetch)
                .outerjoin(Fetch, Fetch.id == DocumentFetch.fetch_id)
                .where(Fetch.id.is_(None))
            ).scalar_one()
        )
        canonical_pages = pages_fetched(session, source_id)
        canonical_known = urls_known(session, source_id)
        stats_consistency = {
            "pages_fetched_total": {
                "stored": int(source.pages_fetched_total or 0),
                "canonical": canonical_pages,
                "delta": int(source.pages_fetched_total or 0) - canonical_pages,
            },
            "urls_known_total": {
                "stored": int(source.urls_known_total or 0),
                "canonical": canonical_known,
                "delta": int(source.urls_known_total or 0) - canonical_known,
            },
        }
        violations = {
            "fetches_missing_documentfetch_link": fetches_missing_documentfetch_link,
            "documents_orphan": documents_orphan,
            "documentfetch_dangling": documentfetch_dangling,
            "chain_broken_count": len(chain_broken_ids),
            "stats_counter_drift": sum(
                1 for item in stats_consistency.values() if item["delta"] != 0
            ),
        }
        ok = all(value == 0 for value in violations.values())
        return {
            "ok": ok,
            "fetches_total": fetches_total,
            "fetches_with_body": fetches_with_body,
            "fetches_body_unchanged_since": len(unchanged_ids) - len(chain_broken_ids),
            "fetches_with_documentfetch_link": fetches_with_documentfetch_link,
            "fetches_missing_documentfetch_link": fetches_missing_documentfetch_link,
            "fetches_missing_documentfetch_urls": missing_rows,
            "documents_total_for_source": documents_for_source(session, source_id),
            "documents_orphan": documents_orphan,
            "documentfetch_dangling": documentfetch_dangling,
            "chain_broken_count": len(chain_broken_ids),
            "stats_consistency": stats_consistency,
            "violations": violations,
        }


def _count(session, query) -> int:
    return int(session.execute(select(func.count()).select_from(query.subquery())).scalar_one())


def _body_chain_resolves(session, fetch_id: uuid.UUID, *, seen: set[uuid.UUID]) -> bool:
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
    return _body_chain_resolves(session, fetch.body_unchanged_since, seen=seen)
