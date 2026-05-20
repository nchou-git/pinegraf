from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import and_, create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, NoSuchModuleError, OperationalError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from backend.db.models import (
    AlumniProfile,
    Base,
    Connection,
    CrawlState,
    Fact,
    Project,
    RawPage,
)

logger = logging.getLogger(__name__)

SQLITE_FALLBACK_URL = "sqlite:///./pinegraf.db"
SQLITE_WARNING = "Running on SQLite - this is dev only. Production deployment must use Postgres."
KEEP_VERDICTS = ("keep",)
SYNTHESIS_VERDICTS = ("keep", "uncertain")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
        class_year: str = "",
        current_company: str | None = None,
        current_title: str | None = None,
        past_companies: list[str] | None = None,
        education: list[str] | None = None,
        bio_summary: str | None = None,
        discovered_via: str = "seed",
        last_parsed_at: datetime | None = None,
    ) -> AlumniProfile:
        with self._session_factory() as session:
            existing = session.execute(
                select(AlumniProfile).where(AlumniProfile.name == name)
            ).scalar_one_or_none()
            if existing:
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

    def raw_page_exists(self, alum_name: str, source_url: str) -> bool:
        with self._session_factory() as session:
            return (
                session.execute(
                    select(RawPage.id).where(
                        RawPage.alum_name == alum_name,
                        RawPage.source_url == source_url,
                    )
                ).first()
                is not None
            )

    def save_raw_page(
        self,
        *,
        alum_name: str,
        source_url: str,
        page_title: str,
        page_text: str,
        fetched_at: datetime | None = None,
    ) -> RawPage:
        with self._session_factory() as session:
            existing = session.execute(
                select(RawPage).where(
                    RawPage.alum_name == alum_name,
                    RawPage.source_url == source_url,
                )
            ).scalar_one_or_none()
            if existing:
                return existing
            raw_page = RawPage(
                alum_name=alum_name,
                source_url=source_url,
                page_title=page_title[:512],
                page_text=page_text,
                fetched_at=fetched_at or _utcnow(),
                parsed_at=None,
            )
            session.add(raw_page)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.execute(
                    select(RawPage).where(
                        RawPage.alum_name == alum_name,
                        RawPage.source_url == source_url,
                    )
                ).scalar_one()
                return existing
            return raw_page

    def list_raw_pages(self) -> list[RawPage]:
        with self._session_factory() as session:
            return list(session.execute(select(RawPage).order_by(RawPage.id.asc())).scalars())

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

    def replace_structured_items(
        self,
        *,
        raw_page_id: int,
        alum_name: str,
        facts: Iterable[dict[str, object]],
        connections: Iterable[dict[str, object]],
        projects: Iterable[dict[str, object]],
    ) -> None:
        with self._session_factory() as session:
            session.query(Fact).filter(Fact.source_raw_page_id == raw_page_id).delete()
            session.query(Connection).filter(Connection.source_raw_page_id == raw_page_id).delete()
            session.query(Project).filter(Project.source_raw_page_id == raw_page_id).delete()

            for fact in facts:
                content = str(fact.get("content", "")).strip()
                if not content:
                    continue
                session.add(
                    Fact(
                        alum_name=alum_name,
                        source_raw_page_id=raw_page_id,
                        category=str(fact.get("category", "general")).strip() or "general",
                        content=content,
                        confidence=str(fact.get("confidence", "low")).strip() or "low",
                        validation_verdict=_clean_verdict(fact.get("validation_verdict")),
                    )
                )
            for connection in connections:
                connected_name = str(connection.get("connected_name", "")).strip()
                if not connected_name:
                    continue
                session.add(
                    Connection(
                        alum_name=alum_name,
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
                        source_raw_page_id=raw_page_id,
                        project_name=project_name,
                        description=str(project.get("description", "")).strip(),
                        validation_verdict=_clean_verdict(project.get("validation_verdict")),
                    )
                )
            session.commit()

    def add_facts(
        self,
        alum_name: str,
        source_raw_page_id: int,
        facts: list[dict[str, object]],
    ) -> None:
        self.replace_structured_items(
            raw_page_id=source_raw_page_id,
            alum_name=alum_name,
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
    ) -> list[Fact]:
        with self._session_factory() as session:
            stmt = (
                select(Fact)
                .options(joinedload(Fact.raw_page))
                .where(Fact.alum_name == alum_name)
                .order_by(Fact.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Fact.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_connections_for_alum(
        self,
        alum_name: str,
        verdicts: tuple[str, ...] | None = None,
    ) -> list[Connection]:
        with self._session_factory() as session:
            stmt = (
                select(Connection)
                .options(joinedload(Connection.raw_page))
                .where(Connection.alum_name == alum_name)
                .order_by(Connection.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Connection.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def list_projects_for_alum(
        self,
        alum_name: str,
        verdicts: tuple[str, ...] | None = None,
    ) -> list[Project]:
        with self._session_factory() as session:
            stmt = (
                select(Project)
                .options(joinedload(Project.raw_page))
                .where(Project.alum_name == alum_name)
                .order_by(Project.id.asc())
            )
            if verdicts:
                stmt = stmt.where(Project.validation_verdict.in_(verdicts))
            return list(session.execute(stmt).scalars())

    def database_context(self, *, verdicts: tuple[str, ...] = KEEP_VERDICTS) -> dict[str, object]:
        return {
            "profiles": [
                {
                    "name": profile.name,
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


def _clean_verdict(value: object) -> str:
    verdict = str(value or "keep").strip().lower()
    if verdict not in {"keep", "uncertain", "drop"}:
        return "uncertain"
    return verdict


def fact_to_dict(fact: Fact) -> dict[str, object]:
    return {
        "id": fact.id,
        "alum_name": fact.alum_name,
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
        "source_raw_page_id": project.source_raw_page_id,
        "source_url": project.raw_page.source_url if project.raw_page else "",
        "project_name": project.project_name,
        "description": project.description,
        "validation_verdict": project.validation_verdict,
    }
