from __future__ import annotations

import gzip
import json
import logging
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any

from sqlalchemy import and_, create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, NoSuchModuleError, OperationalError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from backend.db.models import (
    AlumniProfile,
    AuditEvent,
    Base,
    Connection,
    CrawlState,
    EntityAttribute,
    Fact,
    Project,
    RawPage,
)
from backend.pipeline.position_dates import date_ranges_overlap, parse_position_date

logger = logging.getLogger(__name__)

SQLITE_FALLBACK_URL = "sqlite:///./pinegraf.db"
SQLITE_WARNING = "Running on SQLite - this is dev only. Production deployment must use Postgres."
KEEP_VERDICTS = ("keep",)
SYNTHESIS_VERDICTS = ("keep", "uncertain")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite:")


def _is_postgres_url(database_url: str) -> bool:
    return database_url.startswith("postgresql")


def _sqlite_connect_args(database_url: str) -> dict[str, object]:
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args["check_same_thread"] = False
    return connect_args


class Store:
    def __init__(self, database_url: str) -> None:
        self.requested_database_url = database_url
        self.database_url = database_url
        self.engine: Engine
        self._session_factory: sessionmaker[Session]
        try:
            self._configure_engine(database_url)
        except (ImportError, ModuleNotFoundError, NoSuchModuleError) as exc:
            if not _is_postgres_url(database_url):
                raise
            logger.warning(
                "%s Falling back because the Postgres driver is unavailable: %s",
                SQLITE_WARNING,
                exc,
            )
            self._configure_engine(SQLITE_FALLBACK_URL)

    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    def _configure_engine(self, database_url: str) -> None:
        self.database_url = database_url
        self.engine = create_engine(
            database_url,
            future=True,
            connect_args=_sqlite_connect_args(database_url),
        )
        if self.engine.dialect.name == "sqlite":
            self._install_sqlite_foreign_key_pragma(self.engine)
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    @staticmethod
    def _install_sqlite_foreign_key_pragma(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection: Any, connection_record: object) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    def init_db(self) -> None:
        try:
            Base.metadata.create_all(self.engine)
            self._create_postgres_fts_index()
        except OperationalError as exc:
            if not _is_postgres_url(self.database_url):
                raise
            logger.warning(
                "%s Falling back because Postgres is unavailable: %s", SQLITE_WARNING, exc
            )
            self._configure_engine(SQLITE_FALLBACK_URL)
            Base.metadata.create_all(self.engine)

    def _create_postgres_fts_index(self) -> None:
        if not self.is_postgres:
            return
        ddl = text(
            """
            CREATE INDEX IF NOT EXISTS idx_raw_pages_page_text_fts
            ON raw_pages
            USING GIN (to_tsvector('english', page_text))
            """
        )
        with self.engine.begin() as conn:
            conn.execute(ddl)

    def session(self) -> Session:
        return self._session_factory()

    def upsert_profile(
        self,
        *,
        name: str,
        entity_id: uuid.UUID | str | None = None,
        class_year: str = "",
        current_company: str | None = None,
        current_title: str | None = None,
        past_companies: list[str] | None = None,
        education: list[str] | None = None,
        bio_summary: str | None = None,
        discovered_via: str = "seed",
        last_parsed_at: datetime | None = None,
    ) -> AlumniProfile:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            existing = None
            if entity_uuid is not None:
                existing = session.execute(
                    select(AlumniProfile).where(AlumniProfile.entity_id == entity_uuid)
                ).scalar_one_or_none()
            if existing is None and class_year:
                existing = session.execute(
                    select(AlumniProfile).where(
                        AlumniProfile.name == name,
                        AlumniProfile.class_year == class_year,
                    )
                ).scalar_one_or_none()
            if existing is None and not class_year:
                existing_rows = list(
                    session.execute(
                        select(AlumniProfile).where(AlumniProfile.name == name).limit(2)
                    ).scalars()
                )
                if len(existing_rows) == 1:
                    existing = existing_rows[0]
            if entity_uuid is None:
                if existing is not None and existing.entity_id is not None:
                    entity_uuid = existing.entity_id
                else:
                    context = {"source": discovered_via or "seed"}
                    if class_year:
                        context["class_year"] = class_year
                    entity_uuid = _resolve_entity_in_session(
                        session,
                        name=name,
                        context=context,
                    )
            if existing:
                if entity_uuid is not None:
                    existing.entity_id = entity_uuid
                if class_year:
                    existing.class_year = class_year
                if current_company is not None:
                    existing.current_company = current_company
                if current_title is not None:
                    existing.current_title = current_title
                if past_companies is not None:
                    existing.past_companies = past_companies
                if education is not None:
                    existing.education = education
                if bio_summary is not None:
                    existing.bio_summary = bio_summary
                if discovered_via and not existing.discovered_via:
                    existing.discovered_via = discovered_via
                if last_parsed_at is not None:
                    existing.last_parsed_at = last_parsed_at
                profile = existing
            else:
                profile = AlumniProfile(
                    name=name,
                    entity_id=entity_uuid,
                    class_year=class_year,
                    current_company=current_company or "",
                    current_title=current_title or "",
                    past_companies=past_companies or [],
                    education=education or [],
                    bio_summary=bio_summary or "",
                    discovered_via=discovered_via or "seed",
                    last_parsed_at=last_parsed_at,
                )
                session.add(profile)
            session.commit()
            return profile

    def get_class_year_for_alum(self, alum_name: str) -> str:
        with self._session_factory() as session:
            profile_class = session.execute(
                select(AlumniProfile.class_year).where(AlumniProfile.name == alum_name)
            ).scalar_one_or_none()
            if profile_class:
                return profile_class
            crawl_class = session.execute(
                select(CrawlState.class_year)
                .where(CrawlState.name == alum_name)
                .order_by(CrawlState.id.asc())
                .limit(1)
            ).scalar_one_or_none()
            return crawl_class or ""

    def get_class_year_for_entity(self, entity_id: uuid.UUID | str) -> str:
        entity_uuid = _coerce_uuid(entity_id)
        if entity_uuid is None:
            return ""
        with self._session_factory() as session:
            profile_class = session.execute(
                select(AlumniProfile.class_year).where(AlumniProfile.entity_id == entity_uuid)
            ).scalar_one_or_none()
            return profile_class or ""

    def raw_page_exists(
        self,
        alum_name: str,
        source_url: str,
        entity_id: uuid.UUID | str | None = None,
    ) -> bool:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            where_clause = RawPage.source_url == source_url
            if entity_uuid is not None:
                where_clause = and_(where_clause, RawPage.entity_id == entity_uuid)
            else:
                where_clause = and_(where_clause, RawPage.alum_name == alum_name)
            return session.execute(select(RawPage.id).where(where_clause)).first() is not None

    def save_raw_page(
        self,
        *,
        alum_name: str,
        entity_id: uuid.UUID | str | None = None,
        source_url: str,
        page_title: str,
        page_text: str,
        fetched_at: datetime | None = None,
        content_sha256: str | None = None,
        http_etag: str | None = None,
        http_last_modified: str | None = None,
        http_status: int | None = None,
        raw_html: str | None = None,
        raw_html_gz: bytes | None = None,
        allow_duplicate_snapshot: bool = False,
    ) -> RawPage:
        entity_uuid = _coerce_uuid(entity_id)
        compressed_html = raw_html_gz if raw_html_gz is not None else _gzip_html(raw_html)
        content_hash = content_sha256 or _html_sha256(raw_html)
        with self._session_factory() as session:
            where_clause = RawPage.source_url == source_url
            if entity_uuid is not None:
                where_clause = and_(where_clause, RawPage.entity_id == entity_uuid)
            else:
                where_clause = and_(where_clause, RawPage.alum_name == alum_name)
            existing = None
            if not allow_duplicate_snapshot:
                existing = session.execute(
                    select(RawPage).where(where_clause).order_by(RawPage.id.asc()).limit(1)
                ).scalar_one_or_none()
            if existing:
                if entity_uuid is not None and existing.entity_id is None:
                    existing.entity_id = entity_uuid
                    session.commit()
                return existing
            if entity_uuid is None and alum_name:
                entity_uuid = _profile_entity_for_name(session, alum_name)
            if entity_uuid is None and alum_name:
                entity_uuid = _resolve_entity_in_session(
                    session,
                    name=alum_name,
                    context={"source": "legacy_store"},
                )
            # If still None, store with NULL entity_id (sitemap crawl).
            where_clause = and_(RawPage.source_url == source_url, RawPage.entity_id == entity_uuid)
            raw_page = RawPage(
                alum_name=alum_name,
                entity_id=entity_uuid,
                source_url=source_url,
                page_title=page_title[:512],
                page_text=page_text,
                fetched_at=fetched_at or _utcnow(),
                parsed_at=None,
                content_sha256=content_hash,
                http_etag=http_etag,
                http_last_modified=http_last_modified,
                http_status=http_status,
                raw_html_gz=compressed_html,
            )
            session.add(raw_page)
            try:
                session.commit()
            except IntegrityError:
                if allow_duplicate_snapshot:
                    raise
                session.rollback()
                existing = session.execute(select(RawPage).where(where_clause)).scalar_one()
                return existing
            return raw_page

    def list_raw_pages(self) -> list[RawPage]:
        with self._session_factory() as session:
            return list(session.execute(select(RawPage).order_by(RawPage.id.asc())).scalars())

    def get_latest_raw_page_by_url(self, source_url: str) -> RawPage | None:
        with self._session_factory() as session:
            return session.execute(
                select(RawPage)
                .where(RawPage.source_url == source_url)
                .order_by(RawPage.fetched_at.desc(), RawPage.id.desc())
                .limit(1)
            ).scalar_one_or_none()

    def update_raw_page_fetch_metadata(
        self,
        raw_page_id: int,
        *,
        fetched_at: datetime | None = None,
        http_etag: str | None = None,
        http_last_modified: str | None = None,
        http_status: int | None = None,
    ) -> RawPage | None:
        with self._session_factory() as session:
            raw_page = session.get(RawPage, raw_page_id)
            if raw_page is None:
                return None
            raw_page.fetched_at = fetched_at or _utcnow()
            if http_etag is not None:
                raw_page.http_etag = http_etag
            if http_last_modified is not None:
                raw_page.http_last_modified = http_last_modified
            if http_status is not None:
                raw_page.http_status = http_status
            session.commit()
            return raw_page

    def get_raw_page_html(self, raw_page_id: int) -> str | None:
        with self._session_factory() as session:
            raw_page = session.get(RawPage, raw_page_id)
            if raw_page is None or raw_page.raw_html_gz is None:
                return None
            return gzip.decompress(raw_page.raw_html_gz).decode("utf-8")

    def list_raw_pages_for_alum(self, alum_name: str) -> list[RawPage]:
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(RawPage).where(RawPage.alum_name == alum_name).order_by(RawPage.id.asc())
                ).scalars()
            )

    def list_pages_to_parse(self, *, force: bool = False) -> list[RawPage]:
        with self._session_factory() as session:
            stmt = select(RawPage).order_by(RawPage.alum_name.asc(), RawPage.id.asc())
            if not force:
                stmt = stmt.where(RawPage.parsed_at.is_(None))
            return list(session.execute(stmt).scalars())

    def mark_raw_page_parsed(
        self,
        raw_page_id: int,
        parsed_at: datetime | None = None,
    ) -> None:
        with self._session_factory() as session:
            raw_page = session.get(RawPage, raw_page_id)
            if raw_page is None:
                return
            raw_page.parsed_at = parsed_at or _utcnow()
            session.commit()

    def set_raw_page_entity(self, raw_page_id: int, entity_id: uuid.UUID | str) -> None:
        entity_uuid = _coerce_uuid(entity_id)
        if entity_uuid is None:
            return
        with self._session_factory() as session:
            raw_page = session.get(RawPage, raw_page_id)
            if raw_page is None:
                return
            raw_page.entity_id = entity_uuid
            session.commit()

    def replace_entity_attributes(
        self,
        *,
        entity_id: uuid.UUID | str,
        source_url: str | None,
        attributes: Iterable[dict[str, object]],
    ) -> None:
        entity_uuid = _coerce_uuid(entity_id)
        if entity_uuid is None:
            return
        with self._session_factory() as session:
            session.query(EntityAttribute).filter(
                EntityAttribute.entity_id == entity_uuid,
                EntityAttribute.source_url == source_url,
            ).delete()
            for attribute in attributes:
                attribute_name = str(attribute.get("attribute_name", "")).strip()
                attribute_value = str(attribute.get("attribute_value", "")).strip()
                if not attribute_name or not attribute_value:
                    continue
                session.add(
                    EntityAttribute(
                        entity_id=entity_uuid,
                        attribute_name=attribute_name,
                        attribute_value=attribute_value,
                        source=(
                            str(attribute.get("source") or source_url or "store").strip() or "store"
                        ),
                        source_url=source_url,
                        as_of_date=attribute.get("as_of_date")
                        if isinstance(attribute.get("as_of_date"), date)
                        else None,
                        confidence=str(attribute.get("confidence", "medium")).strip() or "medium",
                        extracted_at=attribute.get("extracted_at")
                        if isinstance(attribute.get("extracted_at"), datetime)
                        else _utcnow(),
                        last_verified_at=attribute.get("last_verified_at")
                        if isinstance(attribute.get("last_verified_at"), datetime)
                        else None,
                        validation_verdict=_clean_verdict(
                            attribute.get("validation_verdict", "keep")
                        ),
                    )
                )
            session.commit()

    def list_entity_attributes(
        self,
        *,
        entity_id: uuid.UUID | str | None = None,
        verdicts: tuple[str, ...] | None = None,
    ) -> list[EntityAttribute]:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            stmt = select(EntityAttribute).order_by(EntityAttribute.id.asc())
            if entity_uuid is not None:
                stmt = stmt.where(EntityAttribute.entity_id == entity_uuid)
            if verdicts:
                stmt = stmt.where(EntityAttribute.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def replace_structured_items(
        self,
        *,
        raw_page_id: int,
        alum_name: str,
        entity_id: uuid.UUID | str | None = None,
        facts: Iterable[dict[str, object]],
        connections: Iterable[dict[str, object]],
        projects: Iterable[dict[str, object]],
    ) -> None:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            if entity_uuid is None:
                raw_page = session.get(RawPage, raw_page_id)
                if raw_page is not None:
                    entity_uuid = raw_page.entity_id
            position_facts = [
                fact
                for fact in facts
                if str(fact.get("category", "general")).strip().lower() == "position"
            ]
            non_position_facts = [
                fact
                for fact in facts
                if str(fact.get("category", "general")).strip().lower() != "position"
            ]

            session.query(Fact).filter(
                Fact.source_raw_page_id == raw_page_id,
                Fact.category != "position",
            ).delete()
            session.query(Connection).filter(Connection.source_raw_page_id == raw_page_id).delete()
            session.query(Project).filter(Project.source_raw_page_id == raw_page_id).delete()

            for fact in non_position_facts:
                content = str(fact.get("content", "")).strip()
                if not content:
                    continue
                session.add(
                    Fact(
                        alum_name=alum_name,
                        entity_id=entity_uuid,
                        source_raw_page_id=raw_page_id,
                        category=str(fact.get("category", "general")).strip() or "general",
                        content=content,
                        confidence=str(fact.get("confidence", "low")).strip() or "low",
                        validation_verdict=_clean_verdict(fact.get("validation_verdict")),
                    )
                )
            self._upsert_position_facts(
                session=session,
                alum_name=alum_name,
                entity_id=entity_uuid,
                source_raw_page_id=raw_page_id,
                position_facts=position_facts,
            )
            for connection in connections:
                connected_name = str(connection.get("connected_name", "")).strip()
                if not connected_name:
                    continue
                session.add(
                    Connection(
                        alum_name=alum_name,
                        entity_id=entity_uuid,
                        connected_name=connected_name,
                        source_raw_page_id=raw_page_id,
                        context=str(connection.get("context", "")).strip(),
                        relationship_type=(
                            str(connection.get("relationship_type", "associate")).strip()
                            or "associate"
                        ),
                        validation_verdict=_clean_verdict(connection.get("validation_verdict")),
                    )
                )
            for project in projects:
                project_name = str(project.get("project_name", "")).strip()
                if not project_name:
                    continue
                session.add(
                    Project(
                        alum_name=alum_name,
                        entity_id=entity_uuid,
                        source_raw_page_id=raw_page_id,
                        project_name=project_name,
                        description=str(project.get("description", "")).strip(),
                        validation_verdict=_clean_verdict(project.get("validation_verdict")),
                    )
                )
            session.commit()

    def _upsert_position_facts(
        self,
        *,
        session: Session,
        alum_name: str,
        entity_id: uuid.UUID | None,
        source_raw_page_id: int,
        position_facts: Iterable[dict[str, object]],
    ) -> None:
        existing_rows = list(
            session.execute(
                select(Fact).where(
                    Fact.alum_name == alum_name,
                    Fact.source_raw_page_id == source_raw_page_id,
                    Fact.category == "position",
                )
            ).scalars()
        )
        by_key: dict[tuple[str, str], Fact] = {}
        for row in existing_rows:
            payload = _parse_position_content(row.content)
            key = (
                _normalize_position_token(payload.get("company")),
                _normalize_position_token(payload.get("title")),
            )
            by_key[key] = row

        seen_keys: set[tuple[str, str]] = set()
        for fact in position_facts:
            content = str(fact.get("content", "")).strip()
            payload = _parse_position_content(content)
            company = str(payload.get("company", "")).strip()
            title = str(payload.get("title", "")).strip()
            if not company or not title:
                continue
            key = (_normalize_position_token(company), _normalize_position_token(title))
            seen_keys.add(key)
            normalized_payload = {
                "company": company,
                "title": title,
                "location": payload.get("location"),
                "start_date": payload.get("start_date"),
                "end_date": payload.get("end_date"),
                "position_type": payload.get("position_type", "other"),
                "is_current": payload.get("end_date") is None,
            }
            existing = by_key.get(key)
            if existing is None:
                session.add(
                    Fact(
                        alum_name=alum_name,
                        entity_id=entity_id,
                        source_raw_page_id=source_raw_page_id,
                        category="position",
                        content=json.dumps(normalized_payload),
                        confidence=str(fact.get("confidence", "low")).strip() or "low",
                        validation_verdict=_clean_verdict(fact.get("validation_verdict")),
                    )
                )
                continue

            existing.content = json.dumps(normalized_payload)
            if entity_id is not None:
                existing.entity_id = entity_id
            existing.confidence = str(fact.get("confidence", "low")).strip() or "low"
            existing.validation_verdict = _clean_verdict(fact.get("validation_verdict"))

        for row in existing_rows:
            payload = _parse_position_content(row.content)
            key = (
                _normalize_position_token(payload.get("company")),
                _normalize_position_token(payload.get("title")),
            )
            if key not in seen_keys:
                session.delete(row)

    def add_facts(
        self,
        alum_name: str,
        source_raw_page_id: int,
        facts: list[dict[str, object]],
        entity_id: uuid.UUID | str | None = None,
    ) -> None:
        self.replace_structured_items(
            raw_page_id=source_raw_page_id,
            alum_name=alum_name,
            entity_id=entity_id,
            facts=facts,
            connections=[],
            projects=[],
        )

    def delete_fact(self, fact_id: int) -> bool:
        with self._session_factory() as session:
            fact = session.get(Fact, fact_id)
            if fact is None:
                return False
            session.delete(fact)
            session.commit()
            return True

    def delete_connection(self, connection_id: int) -> bool:
        with self._session_factory() as session:
            connection = session.get(Connection, connection_id)
            if connection is None:
                return False
            session.delete(connection)
            session.commit()
            return True

    def delete_project(self, project_id: int) -> bool:
        with self._session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                return False
            session.delete(project)
            session.commit()
            return True

    def list_profiles(self) -> list[AlumniProfile]:
        with self._session_factory() as session:
            return list(
                session.execute(select(AlumniProfile).order_by(AlumniProfile.id.asc())).scalars()
            )

    def list_facts(self, verdicts: tuple[str, ...] | None = None) -> list[Fact]:
        with self._session_factory() as session:
            stmt = select(Fact).options(joinedload(Fact.raw_page)).order_by(Fact.id.asc())
            if verdicts:
                stmt = stmt.where(Fact.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_connections(self, verdicts: tuple[str, ...] | None = None) -> list[Connection]:
        with self._session_factory() as session:
            stmt = (
                select(Connection)
                .options(joinedload(Connection.raw_page))
                .order_by(Connection.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Connection.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_projects(self, verdicts: tuple[str, ...] | None = None) -> list[Project]:
        with self._session_factory() as session:
            stmt = select(Project).options(joinedload(Project.raw_page)).order_by(Project.id.asc())
            if verdicts:
                stmt = stmt.where(Project.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_facts_for_alum(
        self,
        alum_name: str,
        verdicts: tuple[str, ...] | None = None,
        *,
        entity_id: uuid.UUID | str | None = None,
    ) -> list[Fact]:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            where_clause = (
                Fact.entity_id == entity_uuid
                if entity_uuid is not None
                else Fact.alum_name == alum_name
            )
            stmt = (
                select(Fact)
                .options(joinedload(Fact.raw_page))
                .where(where_clause)
                .order_by(Fact.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Fact.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_connections_for_alum(
        self,
        alum_name: str,
        verdicts: tuple[str, ...] | None = None,
        *,
        entity_id: uuid.UUID | str | None = None,
    ) -> list[Connection]:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            where_clause = (
                Connection.entity_id == entity_uuid
                if entity_uuid is not None
                else Connection.alum_name == alum_name
            )
            stmt = (
                select(Connection)
                .options(joinedload(Connection.raw_page))
                .where(where_clause)
                .order_by(Connection.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Connection.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_projects_for_alum(
        self,
        alum_name: str,
        verdicts: tuple[str, ...] | None = None,
        *,
        entity_id: uuid.UUID | str | None = None,
    ) -> list[Project]:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            where_clause = (
                Project.entity_id == entity_uuid
                if entity_uuid is not None
                else Project.alum_name == alum_name
            )
            stmt = (
                select(Project)
                .options(joinedload(Project.raw_page))
                .where(where_clause)
                .order_by(Project.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Project.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def get_positions_for_alum(
        self,
        alum_name: str,
        verdicts: frozenset[str] = frozenset(KEEP_VERDICTS),
        *,
        entity_id: uuid.UUID | str | None = None,
    ) -> list[dict[str, object]]:
        entity_uuid = _coerce_uuid(entity_id)
        with self._session_factory() as session:
            where_clause = (
                Fact.entity_id == entity_uuid
                if entity_uuid is not None
                else Fact.alum_name == alum_name
            )
            stmt = (
                select(Fact)
                .options(joinedload(Fact.raw_page))
                .where(where_clause, Fact.category == "position")
                .order_by(Fact.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Fact.validation_verdict.in_(verdicts))
            rows = list(session.execute(stmt).scalars())

        positions: list[dict[str, object]] = []
        for row in rows:
            payload = _parse_position_content(row.content)
            position = {
                "company": payload.get("company", ""),
                "title": payload.get("title", ""),
                "location": payload.get("location"),
                "start_date": payload.get("start_date"),
                "end_date": payload.get("end_date"),
                "position_type": payload.get("position_type", "other"),
                "is_current": payload.get("end_date") is None,
                "source_url": row.raw_page.source_url if row.raw_page else "",
                "merge_group_id": None,
            }
            positions.append(position)

        _assign_merge_group_ids(positions)

        positions.sort(
            key=lambda position: (
                bool(position.get("is_current")),
                _date_ordinal(
                    parse_position_date(_as_str_or_none(position.get("end_date")), is_end_date=True)
                ),
                _date_ordinal(
                    parse_position_date(
                        _as_str_or_none(position.get("start_date")),
                        is_end_date=False,
                    )
                ),
            ),
            reverse=True,
        )
        return positions

    def database_context(self, *, verdicts: tuple[str, ...] = KEEP_VERDICTS) -> dict[str, object]:
        verdict_set = frozenset(verdicts)
        return {
            "profiles": [
                {
                    "name": profile.name,
                    "entity_id": str(profile.entity_id) if profile.entity_id else None,
                    "class_year": profile.class_year,
                    "current_company": profile.current_company,
                    "current_title": profile.current_title,
                    "past_companies": profile.past_companies,
                    "education": profile.education,
                    "bio_summary": profile.bio_summary,
                    "discovered_via": profile.discovered_via,
                    "last_parsed_at": (
                        profile.last_parsed_at.isoformat() if profile.last_parsed_at else None
                    ),
                    "positions": self.get_positions_for_alum(
                        profile.name,
                        verdicts=verdict_set,
                        entity_id=profile.entity_id,
                    ),
                }
                for profile in self.list_profiles()
            ],
            "facts": [fact_to_dict(fact) for fact in self.list_facts(verdicts)],
            "connections": [
                connection_to_dict(connection) for connection in self.list_connections(verdicts)
            ],
            "projects": [project_to_dict(project) for project in self.list_projects(verdicts)],
        }

    def raw_pages_fts_search(self, question: str, *, limit: int = 20) -> list[RawPage]:
        if self.is_postgres:
            return self._postgres_raw_pages_fts_search(question, limit=limit)
        return self._sqlite_raw_pages_fallback(limit=limit)

    def _postgres_raw_pages_fts_search(self, question: str, *, limit: int) -> list[RawPage]:
        sql = text(
            """
            SELECT id
            FROM raw_pages
            WHERE to_tsvector('english', page_text) @@ plainto_tsquery('english', :question)
            ORDER BY ts_rank_cd(
                to_tsvector('english', page_text),
                plainto_tsquery('english', :question)
            ) DESC,
            fetched_at ASC,
            id ASC
            LIMIT :limit
            """
        )
        with self._session_factory() as session:
            page_ids = [
                row[0] for row in session.execute(sql, {"question": question, "limit": limit}).all()
            ]
            if not page_ids:
                return []
            pages = {
                page.id: page
                for page in session.execute(select(RawPage).where(RawPage.id.in_(page_ids)))
                .scalars()
                .all()
            }
            return [pages[page_id] for page_id in page_ids if page_id in pages]

    def _sqlite_raw_pages_fallback(self, *, limit: int) -> list[RawPage]:
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(RawPage)
                    .order_by(RawPage.fetched_at.asc(), RawPage.id.asc())
                    .limit(limit)
                ).scalars()
            )

    def enqueue_crawl(
        self,
        name: str,
        class_year: str,
        depth: int = 0,
        discovered_via: str = "seed",
    ) -> bool:
        with self._session_factory() as session:
            existing = session.execute(
                select(CrawlState).where(
                    and_(CrawlState.name == name, CrawlState.class_year == class_year)
                )
            ).scalar_one_or_none()
            if existing:
                if existing.status in {"partial", "failed"}:
                    existing.status = "pending"
                    existing.depth = min(existing.depth, depth)
                    if discovered_via and not existing.discovered_via:
                        existing.discovered_via = discovered_via
                    session.commit()
                return False
            session.add(
                CrawlState(
                    name=name,
                    class_year=class_year,
                    depth=depth,
                    status="pending",
                    discovered_via=discovered_via or "seed",
                )
            )
            session.commit()
            return True

    def mark_crawl_status(self, name: str, status: str, class_year: str | None = None) -> None:
        with self._session_factory() as session:
            where_clause = CrawlState.name == name
            if class_year is not None:
                where_clause = and_(where_clause, CrawlState.class_year == class_year)
            row = session.execute(select(CrawlState).where(where_clause)).scalars().first()
            if row:
                row.status = status
                session.commit()

    def count_crawl(self) -> int:
        with self._session_factory() as session:
            return int(session.execute(select(func.count()).select_from(CrawlState)).scalar_one())

    def count_crawl_done(self) -> int:
        with self._session_factory() as session:
            return int(
                session.execute(
                    select(func.count()).select_from(CrawlState).where(CrawlState.status == "done")
                ).scalar_one()
            )

    def add_audit_event(
        self,
        *,
        actor: str = "anon",
        action: str,
        payload: dict[str, object],
        created_at: datetime | None = None,
    ) -> AuditEvent:
        with self._session_factory() as session:
            event = AuditEvent(
                actor=actor or "anon",
                action=action,
                payload=payload,
                created_at=created_at or _utcnow(),
            )
            session.add(event)
            session.commit()
            return event

    def list_audit_events(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        actor: str | None = None,
        action: str | None = None,
        limit: int = 100,
        before_id: int | None = None,
    ) -> list[AuditEvent]:
        capped_limit = min(max(limit, 1), 1000)
        with self._session_factory() as session:
            stmt = select(AuditEvent)
            if since is not None:
                stmt = stmt.where(AuditEvent.created_at >= since)
            if until is not None:
                stmt = stmt.where(AuditEvent.created_at <= until)
            if actor:
                stmt = stmt.where(AuditEvent.actor == actor)
            if action:
                stmt = stmt.where(AuditEvent.action == action)
            if before_id is not None:
                stmt = stmt.where(AuditEvent.id < before_id)
            stmt = stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(
                capped_limit
            )
            return list(session.execute(stmt).scalars())


def _clean_verdict(value: object) -> str:
    verdict = str(value or "keep").strip().lower()
    if verdict not in {"keep", "uncertain", "drop"}:
        return "uncertain"
    return verdict


def _coerce_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    cleaned = str(value).strip()
    if not cleaned:
        return None
    return uuid.UUID(cleaned)


def _profile_entity_for_name(session: Session, name: str) -> uuid.UUID | None:
    rows = list(
        session.execute(
            select(AlumniProfile.entity_id)
            .where(AlumniProfile.name == name, AlumniProfile.entity_id.is_not(None))
            .distinct()
            .limit(2)
        ).scalars()
    )
    if len(rows) == 1:
        return rows[0]
    return None


def _resolve_entity_in_session(
    session: Session,
    *,
    name: str,
    context: dict[str, str] | None,
) -> uuid.UUID:
    from backend.resolution.entity_resolver import resolve_or_create

    return resolve_or_create(name, session=session, context=context)


def _html_sha256(raw_html: str | None) -> str | None:
    if raw_html is None:
        return None
    return sha256(raw_html.encode("utf-8")).hexdigest()


def _gzip_html(raw_html: str | None) -> bytes | None:
    if raw_html is None:
        return None
    return gzip.compress(raw_html.encode("utf-8"))


def _normalize_position_token(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _parse_position_content(content: str) -> dict[str, object]:
    try:
        loaded = json.loads(content)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _date_ordinal(value: date | None) -> int:
    if value is None:
        return -1
    return value.toordinal()


def _as_str_or_none(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _assign_merge_group_ids(positions: list[dict[str, object]]) -> None:
    company_groups: dict[str, list[int]] = {}
    for index, position in enumerate(positions):
        company_key = _normalize_position_token(position.get("company"))
        if not company_key:
            continue
        company_groups.setdefault(company_key, []).append(index)

    for company_key, indices in company_groups.items():
        if len(indices) < 2:
            continue
        overlaps: dict[int, set[int]] = {index: set() for index in indices}
        for idx, left in enumerate(indices):
            for right in indices[idx + 1 :]:
                if _positions_overlap(positions[left], positions[right]):
                    overlaps[left].add(right)
                    overlaps[right].add(left)

        visited: set[int] = set()
        for start_index in indices:
            if start_index in visited:
                continue
            stack = [start_index]
            component: list[int] = []
            visited.add(start_index)
            while stack:
                node = stack.pop()
                component.append(node)
                for neighbor in overlaps[node]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        stack.append(neighbor)

            if len(component) < 2:
                continue

            earliest_start = min(
                (
                    parse_position_date(
                        _as_str_or_none(positions[index].get("start_date")),
                        is_end_date=False,
                    )
                    or date.min
                )
                for index in component
            )
            merge_group_id = sha1(
                f"{company_key}:{earliest_start.isoformat()}".encode("utf-8")
            ).hexdigest()[:12]
            for index in component:
                positions[index]["merge_group_id"] = merge_group_id


def _positions_overlap(left: dict[str, object], right: dict[str, object]) -> bool:
    return date_ranges_overlap(
        start_a=parse_position_date(_as_str_or_none(left.get("start_date")), is_end_date=False),
        end_a=parse_position_date(_as_str_or_none(left.get("end_date")), is_end_date=True),
        start_b=parse_position_date(_as_str_or_none(right.get("start_date")), is_end_date=False),
        end_b=parse_position_date(_as_str_or_none(right.get("end_date")), is_end_date=True),
    )


def fact_to_dict(fact: Fact) -> dict[str, object]:
    return {
        "id": fact.id,
        "alum_name": fact.alum_name,
        "entity_id": str(fact.entity_id) if fact.entity_id else None,
        "source_raw_page_id": fact.source_raw_page_id,
        "source_url": fact.raw_page.source_url if fact.raw_page else "",
        "category": fact.category,
        "content": fact.content,
        "confidence": fact.confidence,
        "validation_verdict": fact.validation_verdict,
    }


def connection_to_dict(connection: Connection) -> dict[str, object]:
    return {
        "id": connection.id,
        "alum_name": connection.alum_name,
        "entity_id": str(connection.entity_id) if connection.entity_id else None,
        "connected_name": connection.connected_name,
        "source_raw_page_id": connection.source_raw_page_id,
        "source_url": connection.raw_page.source_url if connection.raw_page else "",
        "context": connection.context,
        "relationship_type": connection.relationship_type,
        "validation_verdict": connection.validation_verdict,
    }


def project_to_dict(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "alum_name": project.alum_name,
        "entity_id": str(project.entity_id) if project.entity_id else None,
        "source_raw_page_id": project.source_raw_page_id,
        "source_url": project.raw_page.source_url if project.raw_page else "",
        "project_name": project.project_name,
        "description": project.description,
        "validation_verdict": project.validation_verdict,
    }
