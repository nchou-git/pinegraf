from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator

JSONList = JSONB().with_variant(JSON(), "sqlite")
JSONDict = JSONB().with_variant(JSON(), "sqlite")
EmbeddingVector = Vector(1536).with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('person', 'organization')",
            name="ck_entities_entity_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector, nullable=True)
    context_embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    aliases: Mapped[list[EntityAlias]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )
    attributes: Mapped[list[EntityAttribute]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_entity_alias_entity_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    entity: Mapped[Entity] = relationship(back_populates="aliases")


class EntityAttribute(Base):
    __tablename__ = "entity_attributes"
    __table_args__ = (
        CheckConstraint(
            "attribute_name IN ("
            "'current_company', 'current_title', 'past_company', 'education', "
            "'class_year', 'bio_summary', 'internship_company', 'internship_location', "
            "'current_employer', 'current_employer_website', 'current_location', "
            "'eship_notes'"
            ")",
            name="ck_entity_attributes_attribute_name",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_entity_attributes_confidence",
        ),
        CheckConstraint(
            "validation_verdict IN ('keep', 'uncertain', 'drop')",
            name="ck_entity_attributes_validation_verdict",
        ),
        Index("ix_entity_attributes_entity_name", "entity_id", "attribute_name"),
        Index("ix_entity_attributes_source", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attribute_name: Mapped[str] = mapped_column(String(64), nullable=False)
    attribute_value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="legacy")
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    validation_verdict: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="keep",
    )

    entity: Mapped[Entity] = relationship(back_populates="attributes")


class RawPage(Base):
    __tablename__ = "raw_pages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Deprecated lookup key; use entity_id for identity.",
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    page_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    page_text: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    parsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    http_etag: Mapped[str | None] = mapped_column(String(256), nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_html_gz: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

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
    entity: Mapped[Entity | None] = relationship()


class HostBoilerplate(Base):
    __tablename__ = "host_boilerplate"

    host: Mapped[str] = mapped_column(Text, primary_key=True)
    prefix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suffix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class AlumniProfile(Base):
    """Canonical profile projection; legacy name is not an identity key."""

    __tablename__ = "alumni_profiles"
    __table_args__ = (UniqueConstraint("name", "class_year", name="uq_alumni_profile_name_class"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Deprecated lookup key; use entity_id for identity.",
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    class_year: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    current_company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    past_companies: Mapped[list[str]] = mapped_column(JSONList, nullable=False, default=list)
    education: Mapped[list[str]] = mapped_column(JSONList, nullable=False, default=list)
    bio_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_parsed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="seed")
    entity: Mapped[Entity | None] = relationship()


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Deprecated lookup key; use entity_id for identity.",
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    confidence_score: Mapped[float | None] = mapped_column(Float(), nullable=True)
    text_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="facts")
    entity: Mapped[Entity | None] = relationship()


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Deprecated lookup key; use entity_id for identity.",
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    connected_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, default="associate")
    confidence_score: Mapped[float | None] = mapped_column(Float(), nullable=True)
    text_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="connections")
    entity: Mapped[Entity | None] = relationship()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    alum_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="Deprecated lookup key; use entity_id for identity.",
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_raw_page_id: Mapped[int] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence_score: Mapped[float | None] = mapped_column(Float(), nullable=True)
    text_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="keep")

    raw_page: Mapped[RawPage] = relationship(back_populates="projects")
    entity: Mapped[Entity | None] = relationship()


class CrawlState(Base):
    __tablename__ = "crawl_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    class_year: Mapped[str] = mapped_column(String(16), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    discovered_via: Mapped[str] = mapped_column(String(255), nullable=False, default="seed")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_actor_created_at", "actor", "created_at"),
        Index("ix_audit_events_action_created_at", "action", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="anon")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        index=True,
    )


class ExtractionCache(Base):
    __tablename__ = "extraction_cache"

    chunk_sha256: Mapped[str] = mapped_column(Text, primary_key=True)
    prompt_version: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    response_json: Mapped[dict[str, object]] = mapped_column(JSONDict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_ts", "ts"),
        Index("ix_llm_usage_model_ts", "model", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dollars: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_page_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_pages.id", ondelete="SET NULL"),
        nullable=True,
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
    )
