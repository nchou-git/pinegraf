"""one active source run per source

Revision ID: 0005_one_active_source_run
Revises: 0004_allow_queued_source_runs
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_one_active_source_run"
down_revision: str | None = "0004_allow_queued_source_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_source_runs_one_active_per_source",
        "source_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_source_runs_one_active_per_source", table_name="source_runs")
