"""drop confidence scoring system

Revision ID: 0023_drop_confidence_system
Revises: 0022_entity_curation
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0023_drop_confidence_system"
down_revision = "0022_entity_curation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_claims_confidence_score_desc", table_name="claims")
    op.drop_column("entity_neighborhood", "confidence")
    op.drop_column("entity_summary", "confidence_avg")
    op.drop_column("claim_evidence", "weight")
    op.drop_column("claims", "confidence")
    op.drop_column("claims", "confidence_score")


def downgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column("claims", sa.Column("confidence", sa.REAL(), nullable=True))
    op.add_column(
        "claim_evidence",
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column("entity_summary", sa.Column("confidence_avg", sa.Float(), nullable=True))
    op.add_column(
        "entity_neighborhood",
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_index("ix_claims_confidence_score_desc", "claims", ["confidence_score"])
