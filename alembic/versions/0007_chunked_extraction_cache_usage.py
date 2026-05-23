from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_chunk_extract_cache_usage"
down_revision: str | None = "0006_host_boilerplate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_dict = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("facts") as batch:
        batch.add_column(sa.Column("confidence_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("text_evidence", sa.Text(), nullable=False, server_default=""))

    with op.batch_alter_table("connections") as batch:
        batch.add_column(sa.Column("confidence_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("text_evidence", sa.Text(), nullable=False, server_default=""))

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("confidence_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("text_evidence", sa.Text(), nullable=False, server_default=""))

    op.create_table(
        "extraction_cache",
        sa.Column("chunk_sha256", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("response_json", json_dict, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("chunk_sha256", "prompt_version", "model"),
    )

    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("dollars", sa.Float(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("raw_page_id", sa.Integer(), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["raw_page_id"], ["raw_pages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_ts", "llm_usage", ["ts"])
    op.create_index("ix_llm_usage_model_ts", "llm_usage", ["model", "ts"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_model_ts", table_name="llm_usage")
    op.drop_index("ix_llm_usage_ts", table_name="llm_usage")
    op.drop_table("llm_usage")
    op.drop_table("extraction_cache")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("text_evidence")
        batch.drop_column("confidence_score")

    with op.batch_alter_table("connections") as batch:
        batch.drop_column("text_evidence")
        batch.drop_column("confidence_score")

    with op.batch_alter_table("facts") as batch:
        batch.drop_column("text_evidence")
        batch.drop_column("confidence_score")
