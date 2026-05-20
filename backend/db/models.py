from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

JSONList = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class RawPage(Base):
    __tablename__ = "raw_pages"
    __table_args__ = (UniqueConstraint("alum_name", "source_url", name="uq_raw_page_alum_url"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    page_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    page_text: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    facts: Mapped[list[Fact]] = relationship(
        back_populates="raw_page",
        cascade="all, delete-orphan",
    )
    connections: Mapped[list[Connection]] = relationship(
        back_populates="raw_page",
        cascade="all, delete-orphan",
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="raw_page",
        cascade="all, delete-orphan",
    )


class AlumniProfile(Base):
    __tablename__ = "alumni_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    current_company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    past_companies: Mapped[list[str]] = mapped_column(JSONList, nullable=False, default=list)
    education: Mapped[list[str]] = mapped_column(JSONList, nullable=False, default=list)
    bio_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="seed")


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="facts")


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    connected_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, default="associate")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="connections")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="projects")


class CrawlState(Base):
    __tablename__ = "crawl_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="seed")
