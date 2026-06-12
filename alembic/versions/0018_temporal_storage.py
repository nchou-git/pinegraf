"""add temporal document and claim columns

Revision ID: 0018_temporal_storage
Revises: 0017_stop_supersede
Create Date: 2026-05-27 05:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_temporal_storage"
down_revision: str | None = "0017_stop_supersede"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "documents",
        sa.Column("superseded_by_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_superseded_by_document_id",
        "documents",
        "documents",
        ["superseded_by_document_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("claims", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
    op.add_column("claims", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "claims",
        sa.Column("superseded_by_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("claims", sa.Column("confidence", sa.REAL(), nullable=True))
    op.create_foreign_key(
        "fk_claims_superseded_by_claim_id",
        "claims",
        "claims",
        ["superseded_by_claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_claims_confidence_range",
        "claims",
        "confidence is null or (confidence >= 0 and confidence <= 1)",
    )
    op.execute(
        "create index ix_claims_subject_predicate_valid "
        "on claims (subject_entity_id, predicate, valid_to nulls first)"
    )


def downgrade() -> None:
    op.execute("drop index if exists ix_claims_subject_predicate_valid")
    op.drop_constraint("ck_claims_confidence_range", "claims", type_="check")
    op.drop_constraint("fk_claims_superseded_by_claim_id", "claims", type_="foreignkey")
    op.drop_column("claims", "confidence")
    op.drop_column("claims", "superseded_by_claim_id")
    op.drop_column("claims", "valid_to")
    op.drop_column("claims", "valid_from")

    op.drop_constraint(
        "fk_documents_superseded_by_document_id",
        "documents",
        type_="foreignkey",
    )
    op.drop_column("documents", "superseded_by_document_id")
    op.drop_column("documents", "valid_to")
    op.drop_column("documents", "valid_from")
