"""add enrichment source kind

Revision ID: 0025_enrichment_source_kind
Revises: 0024_claim_raw_subject_type
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op


revision = "0025_enrichment_source_kind"
down_revision = "0024_claim_raw_subject_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_sources_kind", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_kind",
        "sources",
        "kind in ('domain','file','enrichment')",
    )
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','parse','enrichment')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_source_runs_kind", "source_runs", type_="check")
    op.create_check_constraint(
        "ck_source_runs_kind",
        "source_runs",
        "kind in ('sitemap','seed','parse')",
    )
    op.drop_constraint("ck_sources_kind", "sources", type_="check")
    op.create_check_constraint(
        "ck_sources_kind",
        "sources",
        "kind in ('domain','file')",
    )
