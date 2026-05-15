from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from backend.db.models import AlumniProfile
from backend.db.models import Base


class Store:
    def __init__(self, database_url: str) -> None:
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "", 1)
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url, future=True)
        self._session_factory = sessionmaker(bind=self.engine, class_=Session, expire_on_commit=False)

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    def upsert_profile(
        self,
        *,
        name: str,
        class_year: str,
        current_company: str,
        current_title: str,
        past_companies: list[str],
    ) -> AlumniProfile:
        with self._session_factory() as session:
            existing = (
                session.query(AlumniProfile)
                .filter(AlumniProfile.name == name, AlumniProfile.class_year == class_year)
                .one_or_none()
            )

            if existing:
                existing.current_company = current_company
                existing.current_title = current_title
                existing.past_companies = past_companies
                profile = existing
            else:
                profile = AlumniProfile(
                    name=name,
                    class_year=class_year,
                    current_company=current_company,
                    current_title=current_title,
                    past_companies=past_companies,
                )
                session.add(profile)

            session.commit()
            return profile

    def list_profiles(self) -> list[AlumniProfile]:
        with self._session_factory() as session:
            return session.query(AlumniProfile).order_by(AlumniProfile.id.asc()).all()
