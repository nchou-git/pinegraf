"""allow concurrent source run kinds and add crawl counters

Revision ID: 0015_run_kinds_counters
Revises: 0014_remove_paused_sources
Create Date: 2026-05-27 02:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0015_run_kinds_counters"
down_revision: str | None = "0014_remove_paused_sources"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("pages_fetched_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sources",
        sa.Column("urls_known_total", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE sources
        SET pages_fetched_total = counts.pages_fetched_total,
            urls_known_total = counts.urls_known_total
        FROM (
            SELECT
                sr.source_id,
                count(DISTINCT f.url) FILTER (
                    WHERE f.http_status >= 200
                      AND f.http_status < 300
                      AND f.body_bytes IS NOT NULL
                )::integer AS pages_fetched_total,
                count(DISTINCT f.url)::integer AS urls_known_total
            FROM source_runs sr
            JOIN fetches f ON f.source_run_id = sr.id
            GROUP BY sr.source_id
        ) counts
        WHERE sources.id = counts.source_id
        """
    )
    op.execute(
        """
        UPDATE sources
        SET urls_known_total = GREATEST(urls_known_total, pages_fetched_total)
        """
    )
    op.drop_index("ix_source_runs_one_active_per_source", table_name="source_runs")
    op.create_index(
        "ix_source_runs_one_active_per_source_kind",
        "source_runs",
        ["source_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_source_runs_one_active_per_source_kind", table_name="source_runs")
    op.create_index(
        "ix_source_runs_one_active_per_source",
        "source_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.drop_column("sources", "urls_known_total")
    op.drop_column("sources", "pages_fetched_total")
