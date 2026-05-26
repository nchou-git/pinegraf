"""rename source run parse kind

Revision ID: 0012_parse_source_runs
Revises: 0011_live_logs
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0012_parse_source_runs"
down_revision: str | None = "0011_live_logs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.execute(sa.text("UPDATE source_runs SET kind = 'parse' WHERE kind = 'pipe' || 'line'"))
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','parse')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.execute(sa.text("UPDATE source_runs SET kind = 'pipe' || 'line' WHERE kind = 'parse'"))
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','pipe' || 'line')",
    )
