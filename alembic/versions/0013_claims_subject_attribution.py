from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_claims_subject_attribution"
down_revision = "0012_wikidata_attribute_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_entity_id", sa.Uuid(), nullable=True),
        sa.Column("subject_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("predicate", sa.String(length=128), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(), nullable=True),
        sa.Column("object_name", sa.String(length=255), nullable=True),
        sa.Column("object_value", sa.Text(), nullable=True),
        sa.Column("object_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("source_raw_page_id", sa.Integer(), nullable=False),
        sa.Column("source_chunk_id", sa.Integer(), nullable=True),
        sa.Column("source_chunk_index", sa.Integer(), nullable=True),
        sa.Column("text_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("prompt_version", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "validation_verdict",
            sa.String(length=16),
            nullable=False,
            server_default="keep",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "validation_verdict IN ('keep', 'uncertain', 'drop')",
            name="ck_claims_validation_verdict",
        ),
        sa.ForeignKeyConstraint(["subject_entity_id"], ["entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["object_entity_id"], ["entities.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_raw_page_id"], ["raw_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chunk_id"], ["page_chunks.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_claims_subject_entity", "claims", ["subject_entity_id"])
    op.create_index("ix_claims_object_entity", "claims", ["object_entity_id"])
    op.create_index("ix_claims_source_raw_page", "claims", ["source_raw_page_id"])


def downgrade() -> None:
    op.drop_index("ix_claims_source_raw_page", table_name="claims")
    op.drop_index("ix_claims_object_entity", table_name="claims")
    op.drop_index("ix_claims_subject_entity", table_name="claims")
    op.drop_table("claims")
