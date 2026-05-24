from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.config import get_settings
from backend.db.models import (
    Base,
    Chunk,
    Document,
    DocumentFetch,
    Fetch,
    Source,
    SourceRun,
)

SCHEMA_TABLES = [
    "sources",
    "source_runs",
    "fetches",
    "documents",
    "document_fetches",
    "chunks",
    "extractor_runs",
    "claims_raw",
    "entities",
    "entity_aliases",
    "entity_mentions",
    "claims",
    "claim_evidence",
    "claim_conflicts",
    "human_signals",
    "entity_summary",
    "entity_neighborhood",
]

INITIAL_SOURCES = []


def utc_now() -> datetime:
    return datetime.now(UTC)


def content_digest(body: bytes) -> bytes:
    return hashlib.sha256(body).digest()


def create_engine_for_url(database_url: str) -> Engine:
    connect_args: dict[str, object] = {}
    engine_kwargs: dict[str, object] = {"future": True}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    if engine.dialect.name == "sqlite":
        install_sqlite_foreign_keys(engine)
    return engine


def install_sqlite_foreign_keys(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, connection_record: object) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Store:
    def __init__(self, database_url: str | None = None, *, engine: Engine | None = None) -> None:
        self.database_url = database_url or get_settings().database_url
        self.engine = engine or create_engine_for_url(self.database_url)
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session_factory()

    def ensure_initial_sources(self) -> None:
        for source in INITIAL_SOURCES:
            self.upsert_source(**source)

    def get_source(self, source_id: uuid.UUID) -> Source | None:
        with self.session() as session:
            return session.get(Source, source_id)

    def upsert_source(
        self,
        *,
        kind: str,
        identifier: str,
        trust_weight: float = 0.5,
        display_name: str | None = None,
        notes: str | None = None,
    ) -> Source:
        with self.session() as session:
            existing = session.execute(
                select(Source).where(Source.identifier == identifier)
            ).scalar_one_or_none()
            if existing is not None:
                existing.kind = kind
                existing.trust_weight = trust_weight
                existing.display_name = display_name
                existing.notes = notes
                session.commit()
                return existing
            source = Source(
                kind=kind,
                identifier=identifier,
                trust_weight=trust_weight,
                display_name=display_name,
                notes=notes,
            )
            session.add(source)
            session.commit()
            return source

    def create_source_run(
        self,
        *,
        source_id: uuid.UUID,
        kind: str,
        spec: dict[str, object],
        triggered_by: str,
    ) -> SourceRun:
        with self.session() as session:
            run = SourceRun(
                source_id=source_id,
                kind=kind,
                spec=spec,
                triggered_by=triggered_by,
                status="running",
            )
            session.add(run)
            session.commit()
            return run

    def update_source_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str | None = None,
        stats: dict[str, object] | None = None,
        error_message: str | None = None,
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
            if error_message is not None:
                run.error_message = error_message
            if finished:
                run.finished_at = utc_now()
            session.commit()
            return run

    def get_source_run(self, run_id: uuid.UUID) -> SourceRun | None:
        with self.session() as session:
            return session.get(SourceRun, run_id)

    def add_fetch(
        self,
        *,
        source_run_id: uuid.UUID,
        url: str,
        body_bytes: bytes | None,
        http_status: int | None = None,
        content_type: str | None = None,
        error_message: str | None = None,
    ) -> Fetch:
        digest = content_digest(body_bytes) if body_bytes is not None else None
        with self.session() as session:
            fetch = Fetch(
                source_run_id=source_run_id,
                url=url,
                http_status=http_status,
                content_hash=digest,
                body_bytes=body_bytes,
                content_type=content_type,
                bytes_size=len(body_bytes) if body_bytes is not None else None,
                error_message=error_message,
            )
            session.add(fetch)
            session.commit()
            return fetch

    def get_fetch(self, fetch_id: uuid.UUID) -> Fetch | None:
        with self.session() as session:
            return session.get(Fetch, fetch_id)

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
            )
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
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.execute(
                    select(Document).where(Document.content_hash == content_hash)
                ).scalar_one()
                return existing
            return document

    def pending_fetch_ids(self, *, source_run_id: uuid.UUID | None = None) -> list[uuid.UUID]:
        with self.session() as session:
            query = (
                select(Fetch.id)
                .outerjoin(DocumentFetch, DocumentFetch.fetch_id == Fetch.id)
                .where(DocumentFetch.fetch_id.is_(None))
                .where(Fetch.body_bytes.is_not(None))
                .order_by(Fetch.fetched_at.asc())
            )
            if source_run_id is not None:
                query = query.where(Fetch.source_run_id == source_run_id)
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
        "display_name": source.display_name,
        "notes": source.notes,
        "created_at": source.created_at.isoformat(),
    }
