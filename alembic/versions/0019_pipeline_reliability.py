"""add pipeline reliability metadata

Revision ID: 0019_pipeline_reliability
Revises: 0018_temporal_storage
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0019_pipeline_reliability"
down_revision = "0018_temporal_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("recrawl_interval_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column("sources", sa.Column("last_full_recrawl_at", sa.DateTime(timezone=True)))

    op.add_column("source_runs", sa.Column("stats_updated_at", sa.DateTime(timezone=True)))
    op.execute("update source_runs set stats_updated_at = started_at where stats_updated_at is null")

    op.add_column(
        "fetches",
        sa.Column("body_unchanged_since", sa.Uuid(), nullable=True),
    )
    op.add_column("fetches", sa.Column("parse_skip_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_fetches_body_unchanged_since",
        "fetches",
        "fetches",
        ["body_unchanged_since"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_fetches_body_unchanged_since", "fetches", ["body_unchanged_since"])
    op.create_index("ix_fetches_url_source_run", "fetches", ["source_run_id", "url"])


def downgrade() -> None:
    op.drop_index("ix_fetches_url_source_run", table_name="fetches")
    op.drop_index("ix_fetches_body_unchanged_since", table_name="fetches")
    op.drop_constraint("fk_fetches_body_unchanged_since", "fetches", type_="foreignkey")
    op.drop_column("fetches", "parse_skip_reason")
    op.drop_column("fetches", "body_unchanged_since")
    op.drop_column("source_runs", "stats_updated_at")
    op.drop_column("sources", "last_full_recrawl_at")
    op.drop_column("sources", "recrawl_interval_days")
