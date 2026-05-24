"""intelligence pipeline foundation

Revision ID: 0001_foundation
Revises:
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None

OLD_TABLES = [
    "alumni_profiles",
    "audit_events",
    "audit_runs",
    "claims",
    "connections",
    "crawl_state",
    "entities",
    "entity_aliases",
    "entity_attributes",
    "entity_consolidated",
    "extraction_cache",
    "facts",
    "host_boilerplate",
    "llm_usage",
    "page_chunks",
    "projects",
    "raw_pages",
]

NEW_TABLES_REVERSED = [
    "entity_neighborhood",
    "entity_summary",
    "human_signals",
    "claim_conflicts",
    "claim_evidence",
    "claims",
    "entity_mentions",
    "entity_aliases",
    "entities",
    "claims_raw",
    "extractor_runs",
    "chunks",
    "document_fetches",
    "documents",
    "fetches",
    "source_runs",
    "sources",
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    _drop_old_tables(is_postgres=is_postgres)
    _create_tables(is_postgres=is_postgres)
    _create_indexes(is_postgres=is_postgres)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    for table in NEW_TABLES_REVERSED:
        _drop_table(table, is_postgres=is_postgres)


def _drop_old_tables(*, is_postgres: bool) -> None:
    for table in OLD_TABLES:
        _drop_table(table, is_postgres=is_postgres)


def _drop_table(table: str, *, is_postgres: bool) -> None:
    suffix = " CASCADE" if is_postgres else ""
    op.execute(sa.text(f'DROP TABLE IF EXISTS "{table}"{suffix}'))


def _now_default(is_postgres: bool) -> sa.TextClause:
    return sa.text("now()" if is_postgres else "CURRENT_TIMESTAMP")


def _json_type(is_postgres: bool) -> sa.TypeEngine[object]:
    return postgresql.JSONB() if is_postgres else sa.JSON()


def _vector_type(is_postgres: bool) -> sa.TypeEngine[object]:
    return Vector(1536) if is_postgres else sa.JSON()


def _text_array_type(is_postgres: bool) -> sa.TypeEngine[object]:
    return postgresql.ARRAY(sa.Text()) if is_postgres else sa.JSON()


def _create_tables(*, is_postgres: bool) -> None:
    json_type = _json_type(is_postgres)
    vector_type = _vector_type(is_postgres)
    text_array_type = _text_array_type(is_postgres)
    now = _now_default(is_postgres)

    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False, unique=True),
        sa.Column("trust_weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("display_name", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("kind in ('domain','file','api','human')", name="ck_sources_kind"),
        sa.CheckConstraint(
            "trust_weight >= 0 and trust_weight <= 1",
            name="ck_sources_trust_weight",
        ),
    )

    op.create_table(
        "source_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("spec", json_type, nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("stats", json_type),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "kind in ('sitemap','seed','adhoc','api','manual_upload')",
            name="ck_source_runs_kind",
        ),
        sa.CheckConstraint(
            "status in ('running','complete','failed','partial','cancelled')",
            name="ck_source_runs_status",
        ),
    )

    op.create_table(
        "fetches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_run_id",
            sa.Uuid(),
            sa.ForeignKey("source_runs.id"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("http_status", sa.Integer()),
        sa.Column("content_hash", sa.LargeBinary()),
        sa.Column("body_bytes", sa.LargeBinary()),
        sa.Column("content_type", sa.Text()),
        sa.Column("bytes_size", sa.Integer()),
        sa.Column("error_message", sa.Text()),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("word_count", sa.Integer()),
        sa.Column("first_seen_fetch_id", sa.Uuid(), sa.ForeignKey("fetches.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    op.create_table(
        "document_fetches",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "fetch_id",
            sa.Uuid(),
            sa.ForeignKey("fetches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", vector_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    )

    op.create_table(
        "extractor_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("chunks_processed", sa.Integer(), server_default="0"),
        sa.Column("claims_emitted", sa.Integer(), server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("cost_usd", sa.Numeric(10, 4)),
        sa.CheckConstraint(
            "status in ('running','complete','failed','cancelled')",
            name="ck_extractor_runs_status",
        ),
    )

    op.create_table(
        "claims_raw",
        sa.Column(
            "id",
            sa.Uuid(),
            primary_key=True,
        ),
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("chunks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extractor_run_id",
            sa.Uuid(),
            sa.ForeignKey("extractor_runs.id"),
            nullable=False,
        ),
        sa.Column("subject_text", sa.Text(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_text", sa.Text()),
        sa.Column("object_type", sa.Text()),
        sa.Column("qualifiers", json_type),
        sa.Column("confidence_internal", sa.Float()),
        sa.Column("raw_quote", sa.Text(), nullable=False),
        sa.Column("span_start", sa.Integer()),
        sa.Column("span_end", sa.Integer()),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "object_type in ('person','org','project','place','event','attribute_value','date')",
            name="ck_claims_raw_object_type",
        ),
    )

    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("embedding", vector_type),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "kind in ('person','org','project','place','event')",
            name="ck_entities_kind",
        ),
    )

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("source", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("entity_id", "alias", name="uq_entity_aliases_entity_alias"),
    )

    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "claim_raw_id",
            sa.Uuid(),
            sa.ForeignKey("claims_raw.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), sa.ForeignKey("entities.id"), nullable=False),
        sa.Column("mention_text", sa.Text(), nullable=False),
        sa.Column("resolution_method", sa.Text(), nullable=False),
        sa.Column("resolution_confidence", sa.Float(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "position in ('subject','object')",
            name="ck_entity_mentions_position",
        ),
        sa.CheckConstraint(
            "resolution_method in ('exact_match','alias','embedding','llm','human')",
            name="ck_entity_mentions_resolution_method",
        ),
    )

    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subject_entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id"),
            nullable=False,
        ),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(), sa.ForeignKey("entities.id")),
        sa.Column("object_value", sa.Text()),
        sa.Column("qualifiers", json_type),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column(
            "last_corroborated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now,
        ),
        sa.CheckConstraint(
            "status in ('active','retracted','disputed')",
            name="ck_claims_status",
        ),
        sa.CheckConstraint(
            "object_entity_id is not null or object_value is not null",
            name="ck_claims_object_present",
        ),
    )

    op.create_table(
        "claim_evidence",
        sa.Column(
            "claim_id",
            sa.Uuid(),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "claim_raw_id",
            sa.Uuid(),
            sa.ForeignKey("claims_raw.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    op.create_table(
        "claim_conflicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "claim_a_id",
            sa.Uuid(),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_b_id",
            sa.Uuid(),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("resolution", sa.Text()),
        sa.Column("resolved_by", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "resolution in ('unresolved','claim_a_wins','claim_b_wins',"
            "'both_valid_temporal','both_valid_distinct')",
            name="ck_claim_conflicts_resolution",
        ),
        sa.CheckConstraint("claim_a_id < claim_b_id", name="ck_claim_conflicts_order"),
    )

    op.create_table(
        "human_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("payload", json_type),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "signal_type in ('verify','dispute','correct','add_evidence','redact',"
            "'merge_entities','split_entity','retract_claim')",
            name="ck_human_signals_signal_type",
        ),
        sa.CheckConstraint(
            "target_type in ('claim','entity','mention','evidence')",
            name="ck_human_signals_target_type",
        ),
    )

    op.create_table(
        "entity_summary",
        sa.Column(
            "entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("primary_attributes", json_type),
        sa.Column("connection_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_avg", sa.Float()),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    op.create_table(
        "entity_neighborhood",
        sa.Column(
            "entity_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "neighbor_id",
            sa.Uuid(),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("predicates", text_array_type, nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )


def _create_indexes(*, is_postgres: bool) -> None:
    _create_index("ix_source_runs_source_id", "source_runs", ["source_id"])
    _create_index("ix_source_runs_status", "source_runs", ["status"])
    _create_ordered_index(
        "ix_source_runs_started_at_desc",
        "source_runs",
        "started_at DESC",
        ["started_at"],
        is_postgres=is_postgres,
    )

    _create_index("ix_fetches_source_run_id", "fetches", ["source_run_id"])
    _create_index("ix_fetches_url", "fetches", ["url"])
    _create_ordered_index(
        "ix_fetches_fetched_at_desc",
        "fetches",
        "fetched_at DESC",
        ["fetched_at"],
        is_postgres=is_postgres,
    )
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_fetches_content_hash "
            "ON fetches (content_hash) WHERE content_hash IS NOT NULL"
        )
    else:
        _create_index("ix_fetches_content_hash", "fetches", ["content_hash"])

    _create_index("ix_documents_content_hash", "documents", ["content_hash"])
    _create_index("ix_documents_canonical_url", "documents", ["canonical_url"])
    _create_index("ix_document_fetches_fetch_id", "document_fetches", ["fetch_id"])
    _create_index("ix_chunks_document_id", "chunks", ["document_id"])

    _create_index("ix_claims_raw_chunk_id", "claims_raw", ["chunk_id"])
    _create_index("ix_claims_raw_extractor_run_id", "claims_raw", ["extractor_run_id"])
    _create_index("ix_claims_raw_predicate", "claims_raw", ["predicate"])
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_claims_raw_subject_text ON claims_raw (subject_text text_pattern_ops)"
        )
    else:
        _create_index("ix_claims_raw_subject_text", "claims_raw", ["subject_text"])

    _create_index("ix_entities_kind", "entities", ["kind"])
    _create_index("ix_entity_mentions_claim_raw_id", "entity_mentions", ["claim_raw_id"])
    _create_index("ix_entity_mentions_entity_id", "entity_mentions", ["entity_id"])
    _create_index(
        "ix_entity_mentions_resolution_method",
        "entity_mentions",
        ["resolution_method"],
    )

    _create_index("ix_claims_subject_predicate", "claims", ["subject_entity_id", "predicate"])
    if is_postgres:
        op.execute(
            "CREATE INDEX ix_claims_object_entity_id "
            "ON claims (object_entity_id) WHERE object_entity_id IS NOT NULL"
        )
        op.execute("CREATE INDEX ix_claims_confidence_score_desc ON claims (confidence_score DESC)")
    else:
        _create_index("ix_claims_object_entity_id", "claims", ["object_entity_id"])
        _create_index("ix_claims_confidence_score_desc", "claims", ["confidence_score"])
    _create_index("ix_claims_predicate", "claims", ["predicate"])

    _create_index("ix_claim_evidence_claim_raw_id", "claim_evidence", ["claim_raw_id"])
    _create_index("ix_claim_evidence_source_id", "claim_evidence", ["source_id"])
    _create_index("ix_human_signals_target", "human_signals", ["target_type", "target_id"])
    _create_index("ix_human_signals_user_id", "human_signals", ["user_id"])
    _create_index("ix_human_signals_signal_type", "human_signals", ["signal_type"])
    _create_index(
        "ix_entity_neighborhood_neighbor_id",
        "entity_neighborhood",
        ["neighbor_id"],
    )

    if is_postgres:
        op.execute(
            "CREATE INDEX ix_chunks_embedding_ivfflat "
            "ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
        op.execute(
            "CREATE INDEX ix_entities_canonical_name_trgm "
            "ON entities USING gin (canonical_name gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX ix_entities_embedding_ivfflat "
            "ON entities USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        )
        op.execute(
            "CREATE INDEX ix_entity_aliases_alias_trgm "
            "ON entity_aliases USING gin (alias gin_trgm_ops)"
        )


def _create_index(name: str, table: str, columns: Iterable[str]) -> None:
    op.create_index(name, table, list(columns))


def _create_ordered_index(
    name: str,
    table: str,
    postgres_expression: str,
    fallback_columns: list[str],
    *,
    is_postgres: bool,
) -> None:
    if is_postgres:
        op.execute(f"CREATE INDEX {name} ON {table} ({postgres_expression})")
        return
    op.create_index(name, table, fallback_columns)
