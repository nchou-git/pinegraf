"""tighten source kinds

Revision ID: 0009_tighten_kinds
Revises: 0008_pipeline_runs
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_tighten_kinds"
down_revision: str | None = "0008_pipeline_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_sources_kind", "sources", type_="check")
    op.create_check_constraint("ck_sources_kind", "sources", "kind in ('domain','file')")
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','pipeline')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_kind", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_kind",
        "sources",
        "kind in ('domain','file','api','human')",
    )
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','adhoc','api','manual_upload','pipeline')",
    )
