from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AlumniProfile(Base):
    __tablename__ = "alumni_profiles"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    current_company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    past_companies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    education: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    bio_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    last_researched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    connected_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, default="associate")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")


class CrawlState(Base):
    __tablename__ = "crawl_state"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="")
