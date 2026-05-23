from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0011_hybrid_retrieval"
down_revision = "0010_reconciliation_inference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    embedding_type = Vector(1536) if is_postgres else sa.JSON()
    op.create_table(
        "page_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("raw_page_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["raw_page_id"], ["raw_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("raw_page_id", "chunk_index", name="uq_page_chunks_page_index"),
    )
    op.create_index("ix_page_chunks_raw_page_id", "page_chunks", ["raw_page_id"])
    if is_postgres:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_raw_pages_page_text_trgm "
            "ON raw_pages USING gin (page_text gin_trgm_ops)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_entity_attributes_value_trgm "
            "ON entity_attributes USING gin (attribute_value gin_trgm_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("DROP INDEX IF EXISTS ix_entity_attributes_value_trgm")
        op.execute("DROP INDEX IF EXISTS ix_raw_pages_page_text_trgm")
    op.drop_index("ix_page_chunks_raw_page_id", table_name="page_chunks")
    op.drop_table("page_chunks")
    if is_postgres:
        op.execute("DROP EXTENSION IF EXISTS pg_trgm")
