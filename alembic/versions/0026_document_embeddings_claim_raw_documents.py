"""document embeddings and raw claim document provenance

Revision ID: 0026_doc_embeddings_claim_docs
Revises: 0025_enrichment_source_kind
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op


revision = "0026_doc_embeddings_claim_docs"
down_revision = "0025_enrichment_source_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("embedding", Vector(1536), nullable=True))
    op.add_column("claims_raw", sa.Column("document_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_claims_raw_document_id_documents",
        "claims_raw",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_claims_raw_document_id", "claims_raw", ["document_id"])
    op.execute(
        """
        update claims_raw
        set document_id = chunks.document_id
        from chunks
        where claims_raw.chunk_id = chunks.id
          and claims_raw.document_id is null
        """
    )
    op.alter_column("claims_raw", "chunk_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute("delete from claims_raw where chunk_id is null")
    op.alter_column("claims_raw", "chunk_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_claims_raw_document_id", table_name="claims_raw")
    op.drop_constraint("fk_claims_raw_document_id_documents", "claims_raw", type_="foreignkey")
    op.drop_column("claims_raw", "document_id")
    op.drop_column("documents", "embedding")
