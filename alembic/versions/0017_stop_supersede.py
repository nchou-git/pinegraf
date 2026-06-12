"""replace paused runs with stopped and superseded

Revision ID: 0017_stop_supersede
Revises: 0016_run_pause
Create Date: 2026-05-27 03:35:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0017_stop_supersede"
down_revision: str | None = "0016_run_pause"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.execute("UPDATE source_runs SET status = 'stopped' WHERE status IN ('paused','cancelled')")
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('queued','running','stopped','superseded','complete','failed','partial')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_source_runs_status", "source_runs", type_="check")
    op.execute(
        "UPDATE source_runs SET status = 'cancelled' WHERE status IN ('stopped','superseded')"
    )
    op.create_check_constraint(
        "ck_source_runs_status",
        "source_runs",
        "status in ('queued','running','paused','complete','failed','partial','cancelled')",
    )
