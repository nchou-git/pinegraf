"""source-level robots override

Revision ID: 0006_source_respect_robots
Revises: 0005_one_active_source_run
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_source_respect_robots"
down_revision: str | None = "0005_one_active_source_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "respect_robots",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.alter_column("sources", "respect_robots", server_default=None)


def downgrade() -> None:
    op.drop_column("sources", "respect_robots")
