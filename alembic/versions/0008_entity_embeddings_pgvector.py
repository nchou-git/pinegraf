from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = "0008_entity_embeddings_pgvector"
down_revision = "0007_chunk_extract_cache_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    vector_type = Vector(1536) if is_postgres else sa.JSON()
    op.add_column("entities", sa.Column("name_embedding", vector_type, nullable=True))
    op.add_column("entities", sa.Column("context_embedding", vector_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    op.drop_column("entities", "context_embedding")
    op.drop_column("entities", "name_embedding")
    if is_postgres:
        op.execute("DROP EXTENSION IF EXISTS vector")
