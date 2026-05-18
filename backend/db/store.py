from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import (
    AlumniProfile,
    Base,
    Connection,
    CrawlState,
    Fact,
    Project,
)


class Store:
    def __init__(self, database_url: str) -> None:
        connect_args: dict[str, object] = {}
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "", 1)
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connect_args["check_same_thread"] = False
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self._session_factory = sessionmaker(
            bind=self.engine, class_=Session, expire_on_commit=False
        )

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    def upsert_profile(
        self,
        *,
        name: str,
        class_year: str,
        current_company: str = "",
        current_title: str = "",
        past_companies: list[str] | None = None,
        education: list[str] | None = None,
        bio_summary: str = "",
        depth: int = 0,
        discovered_via: str = "",
    ) -> AlumniProfile:
        with self._session_factory() as session:
            existing = session.query(AlumniProfile).filter(AlumniProfile.name == name).first()
            now = datetime.now(timezone.utc)
            if existing:
                existing.class_year = class_year or existing.class_year
                existing.depth = min(existing.depth, depth)
                if discovered_via and not existing.discovered_via:
                    existing.discovered_via = discovered_via
                if current_company:
                    existing.current_company = current_company
                if current_title:
                    existing.current_title = current_title
                if past_companies is not None:
                    existing.past_companies = past_companies
                if education is not None:
                    existing.education = education
                if bio_summary:
                    existing.bio_summary = bio_summary
                existing.last_researched_at = now
                profile = existing
            else:
                profile = AlumniProfile(
                    name=name,
                    class_year=class_year,
                    current_company=current_company,
                    current_title=current_title,
                    past_companies=past_companies or [],
                    education=education or [],
                    bio_summary=bio_summary,
                    depth=depth,
                    discovered_via=discovered_via,
                    last_researched_at=now,
                )
                session.add(profile)
            session.commit()
            return profile

    def add_facts(self, alum_name: str, facts: list[dict]) -> None:
        if not facts:
            return
        with self._session_factory() as session:
            for f in facts:
                content = f.get("content", "").strip()
                if not content:
                    continue
                existing = (
                    session.execute(
                        select(Fact).where(
                            and_(
                                Fact.alum_name == alum_name,
                                Fact.content == content,
                                Fact.source_url == f.get("source_url", ""),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing:
                    continue
                session.add(
                    Fact(
                        alum_name=alum_name,
                        category=f.get("category", "general"),
                        content=content,
                        source_url=f.get("source_url", ""),
                        confidence=f.get("confidence", "low"),
                    )
                )
            session.commit()

    def add_connections(self, alum_name: str, connections: list[dict]) -> list[str]:
        new_names: list[str] = []
        if not connections:
            return new_names
        with self._session_factory() as session:
            for c in connections:
                connected = c.get("name", "").strip()
                if not connected:
                    continue
                existing = (
                    session.execute(
                        select(Connection).where(
                            and_(
                                Connection.alum_name == alum_name,
                                Connection.connected_name == connected,
                                Connection.context == c.get("context", ""),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing:
                    continue
                session.add(
                    Connection(
                        alum_name=alum_name,
                        connected_name=connected,
                        context=c.get("context", ""),
                        source_url=c.get("source_url", ""),
                        relationship_type=c.get("relationship_type", "associate"),
                    )
                )
                new_names.append(connected)
            session.commit()
        return new_names

    def add_projects(self, alum_name: str, projects: list[dict]) -> None:
        if not projects:
            return
        with self._session_factory() as session:
            for p in projects:
                project_name = p.get("name", "").strip()
                if not project_name:
                    continue
                existing = (
                    session.execute(
                        select(Project).where(
                            and_(
                                Project.alum_name == alum_name,
                                Project.project_name == project_name,
                                Project.source_url == p.get("source_url", ""),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing:
                    continue
                session.add(
                    Project(
                        alum_name=alum_name,
                        project_name=project_name,
                        description=p.get("description", ""),
                        source_url=p.get("source_url", ""),
                    )
                )
            session.commit()

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

    def enqueue_crawl(
        self, name: str, class_year: str, depth: int, discovered_via: str = ""
    ) -> bool:
        """Returns True if newly enqueued, False if already exists."""
        with self._session_factory() as session:
            existing = (
                session.execute(
                    select(CrawlState).where(
                        and_(CrawlState.name == name, CrawlState.class_year == class_year)
                    )
                )
                .scalars()
                .first()
            )
            if existing:
                if existing.status == "partial":
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
                    discovered_via=discovered_via,
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

    def list_pending_by_class(self, class_year: str) -> list[CrawlState]:
        with self._session_factory() as session:
            return list(
                session.execute(
                    select(CrawlState)
                    .where(CrawlState.class_year == class_year, CrawlState.status == "pending")
                    .order_by(CrawlState.depth.asc(), CrawlState.id.asc())
                ).scalars()
            )

    def distinct_class_years_pending(self) -> list[str]:
        with self._session_factory() as session:
            rows = session.execute(
                select(CrawlState.class_year).where(CrawlState.status == "pending").distinct()
            ).scalars()
            return list(rows)

    def count_crawl_by_class(self, class_year: str) -> int:
        with self._session_factory() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(CrawlState)
                    .where(CrawlState.class_year == class_year)
                ).scalar_one()
            )

    def count_crawl_done_by_class(self, class_year: str) -> int:
        with self._session_factory() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(CrawlState)
                    .where(
                        and_(
                            CrawlState.class_year == class_year,
                            CrawlState.status == "done",
                        )
                    )
                ).scalar_one()
            )

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

    def list_profiles(self) -> list[AlumniProfile]:
        with self._session_factory() as session:
            return list(session.query(AlumniProfile).order_by(AlumniProfile.id.asc()).all())

    def list_connections(self) -> list[Connection]:
        with self._session_factory() as session:
            return list(session.query(Connection).order_by(Connection.id.asc()).all())

    def list_projects(self) -> list[Project]:
        with self._session_factory() as session:
            return list(session.query(Project).order_by(Project.id.asc()).all())

    def list_facts(self) -> list[Fact]:
        with self._session_factory() as session:
            return list(session.query(Fact).order_by(Fact.id.asc()).all())
