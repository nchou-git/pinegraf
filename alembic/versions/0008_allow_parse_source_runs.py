"""allow parse source runs

Revision ID: 0008_parse_runs
Revises: 0007_norm_source_ids
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_parse_runs"
down_revision: str | None = "0007_norm_source_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','adhoc','api','manual_upload','parse')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','adhoc','api','manual_upload')",
    )
