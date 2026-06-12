"""add source crawl depth

Revision ID: 0027_source_crawl_depth
Revises: 0026_doc_embeddings_claim_docs
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0027_source_crawl_depth"
down_revision: str | None = "0026_doc_embeddings_claim_docs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("crawl_depth", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_sources_crawl_depth",
        "sources",
        "crawl_depth is null or crawl_depth >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_crawl_depth", "sources", type_="check")
    op.drop_column("sources", "crawl_depth")
