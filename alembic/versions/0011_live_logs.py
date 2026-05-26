"""persist live logs

Revision ID: 0011_live_logs
Revises: 0010_source_status
Create Date: 2026-05-26 04:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0011_live_logs"
down_revision: str | None = "0010_source_status"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "live_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["source_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_live_logs_timestamp", "live_logs", ["timestamp"])
    op.create_index("ix_live_logs_source_run_id", "live_logs", ["source_run_id"])


def downgrade() -> None:
    op.drop_index("ix_live_logs_source_run_id", table_name="live_logs")
    op.drop_index("ix_live_logs_timestamp", table_name="live_logs")
    op.drop_table("live_logs")
