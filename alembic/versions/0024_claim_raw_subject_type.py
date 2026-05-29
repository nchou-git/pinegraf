"""add subject type to raw claims

Revision ID: 0024_claim_raw_subject_type
Revises: 0023_drop_confidence_system
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0024_claim_raw_subject_type"
down_revision = "0023_drop_confidence_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims_raw",
        sa.Column("subject_type", sa.Text(), nullable=False, server_default="person"),
    )
    op.alter_column("claims_raw", "subject_type", server_default=None)


def downgrade() -> None:
    op.drop_column("claims_raw", "subject_type")
