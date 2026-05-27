from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    REAL,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONDict = JSONB()
EmbeddingVector = Vector(1536)
TextArray = ARRAY(Text())


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def uuid4() -> uuid.UUID:
    return uuid.uuid4()


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "kind in ('domain','file')",
            name="ck_sources_kind",
        ),
        CheckConstraint(
            "trust_weight >= 0 and trust_weight <= 1",
            name="ck_sources_trust_weight",
        ),
        CheckConstraint(
            "status in ('active','archived')",
            name="ck_sources_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    identifier: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    trust_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    respect_robots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    pages_fetched_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    urls_known_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recrawl_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    last_full_recrawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SourceRun(Base):
    __tablename__ = "source_runs"
    __table_args__ = (
        CheckConstraint(
            "kind in ('sitemap','seed','parse')",
            name="ck_source_runs_kind",
        ),
        CheckConstraint(
            "status in ('queued','running','stopped','superseded','complete','failed','partial')",
            name="ck_source_runs_status",
        ),
        Index("ix_source_runs_source_id", "source_id"),
        Index("ix_source_runs_status", "status"),
        Index("ix_source_runs_started_at_desc", "started_at"),
        Index(
            "ix_source_runs_one_active_per_source_kind",
            "source_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict[str, object]] = mapped_column(JSONDict, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, object] | None] = mapped_column(JSONDict)
    stats_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class LiveLog(Base):
    __tablename__ = "live_logs"
    __table_args__ = (
        Index("ix_live_logs_timestamp", "timestamp"),
        Index("ix_live_logs_source_run_id", "source_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    level: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_runs.id", ondelete="SET NULL"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_ts", "ts"),
        Index("ix_audit_log_target", "target_table", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text)
    request_ip: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONDict)


class Fetch(Base):
    __tablename__ = "fetches"
    __table_args__ = (
        Index("ix_fetches_source_run_id", "source_run_id"),
        Index("ix_fetches_url", "url"),
        Index("ix_fetches_url_source_run", "source_run_id", "url"),
        Index("ix_fetches_content_hash", "content_hash"),
        Index("ix_fetches_fetched_at_desc", "fetched_at"),
        Index("ix_fetches_body_unchanged_since", "body_unchanged_since"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("source_runs.id"),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    body_unchanged_since: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fetches.id", ondelete="SET NULL"),
    )
    parse_skip_reason: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    bytes_size: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    original_url: Mapped[str | None] = mapped_column(Text)
    redirect_chain: Mapped[list[str] | None] = mapped_column(JSONDict)
    discovery_method: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_canonical_url", "canonical_url"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    cleaned_text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
    )
    word_count: Mapped[int | None] = mapped_column(Integer)
    first_seen_fetch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fetches.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class DocumentFetch(Base):
    __tablename__ = "document_fetches"
    __table_args__ = (Index("ix_document_fetches_fetch_id", "fetch_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    fetch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fetches.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
        Index("ix_chunks_document_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ExtractorRun(Base):
    __tablename__ = "extractor_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running','complete','failed','cancelled')",
            name="ck_extractor_runs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunks_processed: Mapped[int | None] = mapped_column(Integer, default=0)
    claims_emitted: Mapped[int | None] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))


class ClaimRaw(Base):
    __tablename__ = "claims_raw"
    __table_args__ = (
        CheckConstraint(
            "object_type in ('person','org','project','place','event','attribute_value','date')",
            name="ck_claims_raw_object_type",
        ),
        Index("ix_claims_raw_chunk_id", "chunk_id"),
        Index("ix_claims_raw_extractor_run_id", "extractor_run_id"),
        Index("ix_claims_raw_predicate", "predicate"),
        Index("ix_claims_raw_subject_text", "subject_text"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    extractor_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("extractor_runs.id"),
        nullable=False,
    )
    subject_text: Mapped[str] = mapped_column(Text, nullable=False)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_text: Mapped[str | None] = mapped_column(Text)
    object_type: Mapped[str | None] = mapped_column(Text)
    qualifiers: Mapped[dict[str, object] | None] = mapped_column(JSONDict)
    confidence_internal: Mapped[float | None] = mapped_column(Float)
    raw_quote: Mapped[str] = mapped_column(Text, nullable=False)
    span_start: Mapped[int | None] = mapped_column(Integer)
    span_end: Mapped[int | None] = mapped_column(Integer)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(
            "kind in ('person','org','project','place','event')",
            name="ck_entities_kind",
        ),
        Index("ix_entities_kind", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingVector)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_entity_aliases_entity_alias"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntityMention(Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        CheckConstraint(
            "position in ('subject','object')",
            name="ck_entity_mentions_position",
        ),
        CheckConstraint(
            "resolution_method in ('exact_match','alias','embedding','llm','human','new_entity')",
            name="ck_entity_mentions_resolution_method",
        ),
        Index("ix_entity_mentions_claim_raw_id", "claim_raw_id"),
        Index("ix_entity_mentions_entity_id", "entity_id"),
        Index("ix_entity_mentions_resolution_method", "resolution_method"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    claim_raw_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims_raw.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id"),
        nullable=False,
    )
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_method: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntityDisambiguationCandidate(Base):
    __tablename__ = "entity_disambiguation_candidates"
    __table_args__ = (
        CheckConstraint(
            "llm_decision in ('merged','split','near_miss_review')",
            name="ck_entity_disambiguation_candidates_llm_decision",
        ),
        CheckConstraint(
            "review_decision is null or review_decision in ('confirm','merge','split')",
            name="ck_entity_disambiguation_candidates_review_decision",
        ),
        Index("ix_entity_disambiguation_candidates_mention_id", "mention_id"),
        Index(
            "ix_entity_disambiguation_candidates_candidate_entity_id",
            "candidate_entity_id",
        ),
        Index("ix_entity_disambiguation_candidates_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    mention_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entity_mentions.id", ondelete="SET NULL"),
    )
    candidate_entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    llm_decision: Mapped[str] = mapped_column(Text, nullable=False)
    llm_reasoning: Mapped[str | None] = mapped_column(Text)
    name_similarity_score: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_decision: Mapped[str | None] = mapped_column(Text)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "status in ('active','retracted','disputed')",
            name="ck_claims_status",
        ),
        CheckConstraint(
            "object_entity_id is not null or object_value is not null",
            name="ck_claims_object_present",
        ),
        CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="ck_claims_confidence_range",
        ),
        Index("ix_claims_subject_predicate", "subject_entity_id", "predicate"),
        Index(
            "ix_claims_subject_predicate_valid",
            "subject_entity_id",
            "predicate",
            text("valid_to NULLS FIRST"),
        ),
        Index("ix_claims_object_entity_id", "object_entity_id"),
        Index("ix_claims_predicate", "predicate"),
        Index("ix_claims_confidence_score_desc", "confidence_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id"),
        nullable=False,
    )
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id"),
    )
    object_value: Mapped[str | None] = mapped_column(Text)
    qualifiers: Mapped[dict[str, object] | None] = mapped_column(JSONDict)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    confidence: Mapped[float | None] = mapped_column(REAL)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_corroborated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        Index("ix_claim_evidence_claim_raw_id", "claim_raw_id"),
        Index("ix_claim_evidence_source_id", "source_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        primary_key=True,
    )
    claim_raw_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims_raw.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id"),
        nullable=False,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ClaimConflict(Base):
    __tablename__ = "claim_conflicts"
    __table_args__ = (
        CheckConstraint(
            "resolution in ('unresolved','claim_a_wins','claim_b_wins',"
            "'both_valid_temporal','both_valid_distinct')",
            name="ck_claim_conflicts_resolution",
        ),
        CheckConstraint("claim_a_id < claim_b_id", name="ck_claim_conflicts_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    claim_a_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_b_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class HumanSignal(Base):
    __tablename__ = "human_signals"
    __table_args__ = (
        CheckConstraint(
            "signal_type in ('verify','dispute','correct','add_evidence','redact',"
            "'merge_entities','split_entity','retract_claim')",
            name="ck_human_signals_signal_type",
        ),
        CheckConstraint(
            "target_type in ('claim','entity','mention','evidence')",
            name="ck_human_signals_target_type",
        ),
        Index("ix_human_signals_target", "target_type", "target_id"),
        Index("ix_human_signals_user_id", "user_id"),
        Index("ix_human_signals_signal_type", "signal_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONDict)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntitySummary(Base):
    __tablename__ = "entity_summary"

    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    primary_attributes: Mapped[dict[str, object] | None] = mapped_column(JSONDict)
    connection_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_avg: Mapped[float | None] = mapped_column(Float)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EntityNeighborhood(Base):
    __tablename__ = "entity_neighborhood"
    __table_args__ = (Index("ix_entity_neighborhood_neighbor_id", "neighbor_id"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    neighbor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    predicates: Mapped[list[str]] = mapped_column(TextArray, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
