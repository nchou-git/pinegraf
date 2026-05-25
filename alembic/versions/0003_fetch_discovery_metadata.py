"""fetch discovery metadata

Revision ID: 0003_fetch_discovery_metadata
Revises: 0002_new_entity_resolution
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_fetch_discovery_metadata"
down_revision = "0002_new_entity_resolution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fetches", sa.Column("original_url", sa.Text()))
    op.add_column("fetches", sa.Column("redirect_chain", postgresql.JSONB()))
    op.add_column("fetches", sa.Column("discovery_method", sa.Text()))


def downgrade() -> None:
    op.drop_column("fetches", "discovery_method")
    op.drop_column("fetches", "redirect_chain")
    op.drop_column("fetches", "original_url")
