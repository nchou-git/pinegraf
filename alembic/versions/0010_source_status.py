"""move source status out of notes

Revision ID: 0010_source_status
Revises: 0009_tighten_kinds
Create Date: 2026-05-26 03:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0010_source_status"
down_revision: str | None = "0009_tighten_kinds"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_sources_status",
        "sources",
        "status in ('active','paused','archived')",
    )
    op.execute(
        """
        UPDATE sources
        SET
            status = CASE
                WHEN notes = 'status:archived' OR notes LIKE E'status:archived\n%' THEN 'archived'
                WHEN notes = 'status:paused' OR notes LIKE E'status:paused\n%' THEN 'paused'
                ELSE 'active'
            END,
            notes = CASE
                WHEN notes = 'status:archived' OR notes = 'status:paused' THEN NULL
                WHEN notes LIKE E'status:archived\n%' THEN NULLIF(
                    substr(notes, length('status:archived') + 2),
                    ''
                )
                WHEN notes LIKE E'status:paused\n%' THEN NULLIF(
                    substr(notes, length('status:paused') + 2),
                    ''
                )
                ELSE notes
            END
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE sources
        SET notes = CASE
            WHEN status = 'archived' THEN
                CASE WHEN notes IS NULL OR notes = '' THEN 'status:archived'
                     ELSE 'status:archived' || E'\n' || notes END
            WHEN status = 'paused' THEN
                CASE WHEN notes IS NULL OR notes = '' THEN 'status:paused'
                     ELSE 'status:paused' || E'\n' || notes END
            ELSE notes
        END
        """
    )
    op.drop_constraint("ck_sources_status", "sources", type_="check")
    op.drop_column("sources", "status")
