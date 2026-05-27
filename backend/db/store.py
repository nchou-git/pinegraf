from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from sqlalchemy import create_engine, delete, func, inspect, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings
from backend.db.models import (
    AuditLog,
    Base,
    Chunk,
    Claim,
    ClaimConflict,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Fetch,
    Source,
    SourceRun,
)
from backend.db.stats_queries import pages_fetched, pending_fetch_ids, urls_known
from backend.source_identifiers import normalize_identifier

SCHEMA_TABLES = [
    "sources",
    "source_runs",
    "live_logs",
    "audit_log",
    "fetches",
    "documents",
    "document_fetches",
    "chunks",
    "extractor_runs",
    "claims_raw",
    "entities",
    "entity_aliases",
    "entity_mentions",
    "entity_disambiguation_candidates",
    "claims",
    "claim_evidence",
    "claim_conflicts",
    "human_signals",
    "entity_summary",
    "entity_neighborhood",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


def content_digest(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def create_engine_for_url(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


class Store:
    def __init__(self, database_url: str | None = None, *, engine: Engine | None = None) -> None:
        self.database_url = database_url or get_settings().database_url
        self.engine = engine or create_engine_for_url(self.database_url)
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    def session(self) -> Session:
        return self._session_factory()

    def get_source(self, source_id: uuid.UUID) -> Source | None:
        with self.session() as session:
            return session.get(Source, source_id)

    def upsert_source(
        self,
        *,
        kind: str,
        identifier: str,
        trust_weight: float = 0.5,
        respect_robots: bool = True,
        status: str = "active",
        display_name: str | None = None,
        notes: str | None = None,
        audit_actor: str | None = None,
        audit_request_ip: str | None = None,
        audit_payload: dict[str, object] | None = None,
    ) -> Source:
        identifier = normalize_identifier(kind, identifier)
        if not identifier:
            raise ValueError("identifier is required")
        with self.session() as session:
            existing = session.execute(
                select(Source).where(Source.identifier == identifier)
            ).scalar_one_or_none()
            if existing is not None:
                existing.kind = kind
                existing.trust_weight = trust_weight
                existing.respect_robots = respect_robots
                existing.status = status
                existing.display_name = display_name
                existing.notes = notes
                if audit_payload is not None:
                    session.add(
                        AuditLog(
                            action="source.create",
                            target_table="sources",
                            target_id=str(existing.id),
                            actor=audit_actor,
                            request_ip=audit_request_ip,
                            payload=audit_payload,
                        )
                    )
                session.commit()
                return existing
            source = Source(
                kind=kind,
                identifier=identifier,
                trust_weight=trust_weight,
                respect_robots=respect_robots,
                status=status,
                display_name=display_name,
                notes=notes,
                recrawl_interval_days=get_settings().recrawl_default_days,
            )
            session.add(source)
            session.flush()
            if audit_payload is not None:
                session.add(
                    AuditLog(
                        action="source.create",
                        target_table="sources",
                        target_id=str(source.id),
                        actor=audit_actor,
                        request_ip=audit_request_ip,
                        payload=audit_payload,
                    )
                )
            session.commit()
            return source

    def create_source_run(
        self,
        *,
        source_id: uuid.UUID,
        kind: str,
        spec: dict[str, object],
        triggered_by: str,
        status: str = "running",
        audit_action: str | None = None,
        audit_actor: str | None = None,
        audit_request_ip: str | None = None,
        audit_payload: dict[str, object] | None = None,
    ) -> SourceRun:
        with self.session() as session:
            run = SourceRun(
                source_id=source_id,
                kind=kind,
                spec=spec,
                triggered_by=triggered_by,
                status=status,
            )
            session.add(run)
            session.flush()
            if audit_action is not None:
                session.add(
                    AuditLog(
                        action=audit_action,
                        target_table="source_runs",
                        target_id=str(run.id),
                        actor=audit_actor,
                        request_ip=audit_request_ip,
                        payload=audit_payload,
                    )
                )
            session.commit()
            return run

    def update_source_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str | None = None,
        stats: dict[str, object] | None = None,
        error_message: str | None = None,
        clear_finished: bool = False,
        finished: bool = False,
    ) -> SourceRun | None:
        with self.session() as session:
            run = session.get(SourceRun, run_id)
            if run is None:
                return None
            if status is not None:
                run.status = status
            if stats is not None:
                run.stats = stats
                run.stats_updated_at = utc_now()
            if error_message is not None:
                run.error_message = error_message
            if clear_finished:
                run.finished_at = None
            if finished:
                run.finished_at = utc_now()
            session.commit()
            return run

    def patch_source_run_spec(
        self, run_id: uuid.UUID, values: dict[str, object]
    ) -> SourceRun | None:
        with self.session() as session:
            run = session.get(SourceRun, run_id)
            if run is None:
                return None
            run.spec = {**dict(run.spec or {}), **values}
            session.commit()
            return run

    def refresh_source_crawl_counters(
        self,
        source_id: uuid.UUID,
        *,
        urls_known_total: int | None = None,
    ) -> tuple[int, int]:
        with self.session() as session:
            pages_fetched_total = pages_fetched(session, source_id)
            known_total = urls_known(session, source_id)
            source = session.get(Source, source_id)
            if source is None:
                return pages_fetched_total, max(pages_fetched_total, known_total)
            known = max(pages_fetched_total, known_total)
            source.pages_fetched_total = pages_fetched_total
            source.urls_known_total = known
            session.commit()
            return pages_fetched_total, known

    def mark_source_full_recrawl_complete(self, source_id: uuid.UUID) -> None:
        with self.session() as session:
            source = session.get(Source, source_id)
            if source is None:
                return
            source.last_full_recrawl_at = utc_now()
            session.commit()

    def get_source_run(self, run_id: uuid.UUID) -> SourceRun | None:
        with self.session() as session:
            return session.get(SourceRun, run_id)

    def delete_document(self, document_id: uuid.UUID) -> bool:
        with self.session() as session:
            document = session.get(Document, document_id)
            if document is None:
                return False
            impacted_claim_ids = list(
                session.execute(
                    select(ClaimEvidence.claim_id)
                    .join(ClaimRaw, ClaimRaw.id == ClaimEvidence.claim_raw_id)
                    .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
                    .where(Chunk.document_id == document_id)
                ).scalars()
            )
            session.delete(document)
            session.flush()
            orphan_claim_ids = list(
                session.execute(
                    select(Claim.id)
                    .where(Claim.id.in_(impacted_claim_ids))
                    .where(
                        ~select(ClaimEvidence.claim_id)
                        .where(ClaimEvidence.claim_id == Claim.id)
                        .exists()
                    )
                ).scalars()
            )
            if orphan_claim_ids:
                session.execute(
                    delete(ClaimConflict)
                    .where(
                        or_(
                            ClaimConflict.claim_a_id.in_(orphan_claim_ids),
                            ClaimConflict.claim_b_id.in_(orphan_claim_ids),
                        )
                    )
                    .execution_options(synchronize_session=False)
                )
                session.execute(
                    delete(Claim)
                    .where(Claim.id.in_(orphan_claim_ids))
                    .execution_options(synchronize_session=False)
                )
            session.commit()
            return True

    def add_fetch(
        self,
        *,
        source_run_id: uuid.UUID,
        url: str,
        body_bytes: bytes | None,
        http_status: int | None = None,
        content_type: str | None = None,
        content_hash: bytes | None = None,
        body_unchanged_since: uuid.UUID | None = None,
        parse_skip_reason: str | None = None,
        error_message: str | None = None,
        original_url: str | None = None,
        redirect_chain: list[str] | None = None,
        discovery_method: str | None = None,
    ) -> Fetch:
        normalized_status = (
            http_status
            if http_status is not None
            else (200 if body_bytes is not None and error_message is None else None)
        )
        digest = (
            content_hash
            if content_hash is not None
            else (content_digest(body_bytes) if body_bytes is not None else None)
        )
        with self.session() as session:
            fetch = Fetch(
                source_run_id=source_run_id,
                url=url,
                http_status=normalized_status,
                content_hash=digest,
                body_bytes=body_bytes,
                body_unchanged_since=body_unchanged_since,
                parse_skip_reason=parse_skip_reason,
                content_type=content_type,
                bytes_size=len(body_bytes) if body_bytes is not None else None,
                error_message=error_message,
                original_url=original_url,
                redirect_chain=redirect_chain,
                discovery_method=discovery_method,
            )
            session.add(fetch)
            session.commit()
            return fetch

    def get_fetch(self, fetch_id: uuid.UUID) -> Fetch | None:
        with self.session() as session:
            return session.get(Fetch, fetch_id)

    def latest_successful_fetch_for_url(
        self,
        *,
        source_id: uuid.UUID,
        url: str,
    ) -> Fetch | None:
        with self.session() as session:
            return session.execute(
                select(Fetch)
                .join(SourceRun, SourceRun.id == Fetch.source_run_id)
                .where(SourceRun.source_id == source_id)
                .where(Fetch.url == url)
                .where(Fetch.http_status >= 200)
                .where(Fetch.http_status < 300)
                .where(Fetch.content_hash.is_not(None))
                .order_by(Fetch.fetched_at.desc())
                .limit(1)
            ).scalar_one_or_none()

    def update_fetch_hash(self, fetch_id: uuid.UUID, digest: bytes) -> None:
        with self.session() as session:
            fetch = session.get(Fetch, fetch_id)
            if fetch is not None:
                fetch.content_hash = digest
                session.commit()

    def get_document_by_hash(self, digest: bytes) -> Document | None:
        with self.session() as session:
            return session.execute(
                select(Document).where(Document.content_hash == digest)
            ).scalar_one_or_none()

    def link_document_fetch(self, document_id: uuid.UUID, fetch_id: uuid.UUID) -> None:
        with self.session() as session:
            existing = session.get(
                DocumentFetch,
                {"document_id": document_id, "fetch_id": fetch_id},
            )
            if existing is not None:
                return
            session.add(DocumentFetch(document_id=document_id, fetch_id=fetch_id))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

    def create_document_with_chunks(
        self,
        *,
        content_hash: bytes,
        cleaned_text: str,
        title: str | None,
        canonical_url: str | None,
        language: str | None,
        word_count: int,
        first_seen_fetch_id: uuid.UUID,
        chunks: Sequence[tuple[str, int, list[float] | None]],
        valid_from: datetime | None = None,
    ) -> Document:
        with self.session() as session:
            document = Document(
                content_hash=content_hash,
                cleaned_text=cleaned_text,
                title=title,
                canonical_url=canonical_url,
                language=language,
                word_count=word_count,
                first_seen_fetch_id=first_seen_fetch_id,
                valid_from=valid_from,
            )
            try:
                session.add(document)
                session.flush()
                for ordinal, (text, token_count, embedding) in enumerate(chunks):
                    session.add(
                        Chunk(
                            document_id=document.id,
                            ordinal=ordinal,
                            text=text,
                            token_count=token_count,
                            embedding=embedding,
                        )
                    )
                session.add(DocumentFetch(document_id=document.id, fetch_id=first_seen_fetch_id))
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.execute(
                    select(Document).where(Document.content_hash == content_hash)
                ).scalar_one_or_none()
                if existing is None:
                    raise exc
                return existing
            return document

    def pending_fetch_ids(
        self,
        *,
        source_id: uuid.UUID | None = None,
        fetch_ids: Sequence[uuid.UUID] | None = None,
        snapshot_at: datetime | None = None,
    ) -> list[uuid.UUID]:
        with self.session() as session:
            return pending_fetch_ids(
                session,
                source_id=source_id,
                fetch_ids=list(fetch_ids) if fetch_ids is not None else None,
                snapshot_at=snapshot_at,
            )

    def fetch_ids_for_source(
        self,
        source_id: uuid.UUID,
        *,
        fetch_ids: Sequence[uuid.UUID] | None = None,
        snapshot_at: datetime | None = None,
    ) -> list[uuid.UUID]:
        with self.session() as session:
            query = (
                select(Fetch.id)
                .join(SourceRun, SourceRun.id == Fetch.source_run_id)
                .where(SourceRun.source_id == source_id)
                .where(Fetch.body_bytes.is_not(None))
                .order_by(Fetch.fetched_at.asc())
            )
            if fetch_ids is not None:
                query = query.where(Fetch.id.in_(fetch_ids))
            if snapshot_at is not None:
                query = query.where(Fetch.fetched_at <= snapshot_at)
            return list(session.execute(query).scalars())

    def table_counts(self, tables: Iterable[str] = SCHEMA_TABLES) -> dict[str, int]:
        inspector = inspect(self.engine)
        existing = set(inspector.get_table_names())
        counts: dict[str, int] = {}
        with self.session() as session:
            for table_name in tables:
                if table_name not in existing:
                    counts[table_name] = 0
                    continue
                table = Base.metadata.tables[table_name]
                counts[table_name] = int(
                    session.execute(select(func.count()).select_from(table)).scalar_one()
                )
        return counts


def source_to_dict(source: Source) -> dict[str, object]:
    return {
        "id": str(source.id),
        "kind": source.kind,
        "identifier": source.identifier,
        "trust_weight": source.trust_weight,
        "respect_robots": source.respect_robots,
        "status": source.status,
        "pages_fetched_total": source.pages_fetched_total,
        "urls_known_total": source.urls_known_total,
        "recrawl_interval_days": source.recrawl_interval_days,
        "last_full_recrawl_at": (
            source.last_full_recrawl_at.isoformat() if source.last_full_recrawl_at else None
        ),
        "display_name": source.display_name,
        "notes": source.notes,
        "created_at": source.created_at.isoformat(),
    }
