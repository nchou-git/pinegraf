"""remove paused source status

Revision ID: 0014_remove_paused_sources
Revises: 0013_audit_log
Create Date: 2026-05-27 01:45:00.000000
"""

from __future__ import annotations

from alembic import op

revision: str = "0014_remove_paused_sources"
down_revision: str | None = "0013_audit_log"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("UPDATE sources SET status = 'active' WHERE status = 'paused'")
    op.drop_constraint("ck_sources_status", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_status",
        "sources",
        "status in ('active','archived')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_status", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_status",
        "sources",
        "status in ('active','paused','archived')",
    )
