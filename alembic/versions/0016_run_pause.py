"""allow paused source runs

Revision ID: 0016_run_pause
Revises: 0015_run_kinds_counters
Create Date: 2026-05-27 03:25:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0016_run_pause"
down_revision: str | None = "0015_run_kinds_counters"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('queued','running','paused','complete','failed','partial','cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE source_runs SET status = 'cancelled' WHERE status = 'paused'")
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('queued','running','complete','failed','partial','cancelled')",
    )
