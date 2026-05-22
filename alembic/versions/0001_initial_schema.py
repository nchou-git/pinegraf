from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_list = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "alumni_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("class_year", sa.String(length=16), nullable=False),
        sa.Column("current_company", sa.String(length=255), nullable=False),
        sa.Column("current_title", sa.String(length=255), nullable=False),
        sa.Column("past_companies", json_list, nullable=False),
        sa.Column("education", json_list, nullable=False),
        sa.Column("bio_summary", sa.Text(), nullable=False),
        sa.Column("last_parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discovered_via", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_alumni_profiles_name"),
    )
    op.create_index("ix_alumni_profiles_name", "alumni_profiles", ["name"])

    op.create_table(
        "crawl_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("class_year", sa.String(length=16), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("discovered_via", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crawl_state_name", "crawl_state", ["name"])

    op.create_table(
        "raw_pages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alum_name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("page_title", sa.String(length=512), nullable=False),
        sa.Column("page_text", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alum_name", "source_url", name="uq_raw_page_alum_url"),
    )
    op.create_index("ix_raw_pages_alum_name", "raw_pages", ["alum_name"])

    op.create_table(
        "connections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alum_name", sa.String(length=255), nullable=False),
        sa.Column("connected_name", sa.String(length=255), nullable=False),
        sa.Column("source_raw_page_id", sa.Integer(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("validation_verdict", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_raw_page_id"],
            ["raw_pages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_connections_alum_name", "connections", ["alum_name"])
    op.create_index("ix_connections_connected_name", "connections", ["connected_name"])

    op.create_table(
        "facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alum_name", sa.String(length=255), nullable=False),
        sa.Column("source_raw_page_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("validation_verdict", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_raw_page_id"],
            ["raw_pages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_facts_alum_name", "facts", ["alum_name"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alum_name", sa.String(length=255), nullable=False),
        sa.Column("source_raw_page_id", sa.Integer(), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("validation_verdict", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_raw_page_id"],
            ["raw_pages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_alum_name", "projects", ["alum_name"])


def downgrade() -> None:
    op.drop_index("ix_projects_alum_name", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_facts_alum_name", table_name="facts")
    op.drop_table("facts")
    op.drop_index("ix_connections_connected_name", table_name="connections")
    op.drop_index("ix_connections_alum_name", table_name="connections")
    op.drop_table("connections")
    op.drop_index("ix_raw_pages_alum_name", table_name="raw_pages")
    op.drop_table("raw_pages")
    op.drop_index("ix_crawl_state_name", table_name="crawl_state")
    op.drop_table("crawl_state")
    op.drop_index("ix_alumni_profiles_name", table_name="alumni_profiles")
    op.drop_table("alumni_profiles")
