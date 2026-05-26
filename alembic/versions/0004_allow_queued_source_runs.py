"""allow queued source runs

Revision ID: 0004_allow_queued_source_runs
Revises: 0003_fetch_discovery_metadata
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_allow_queued_source_runs"
down_revision: str | None = "0003_fetch_discovery_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('queued','running','complete','failed','partial','cancelled')",
    )


def downgrade() -> None:
    op.execute("update source_runs set status = 'running' where status = 'queued'")
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('running','complete','failed','partial','cancelled')",
    )
