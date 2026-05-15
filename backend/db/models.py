from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class Base(DeclarativeBase):
    pass


class AlumniProfile(Base):
    __tablename__ = "alumni_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False)
    current_company: Mapped[str] = mapped_column(String(255), nullable=False)
    current_title: Mapped[str] = mapped_column(String(255), nullable=False)
    past_companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
