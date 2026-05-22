from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_raw_page_snapshots"
down_revision: str | None = "0002_entity_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_pages") as batch:
        batch.add_column(sa.Column("content_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("http_etag", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("http_last_modified", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("http_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("raw_html_gz", sa.LargeBinary(), nullable=True))
        batch.drop_constraint("uq_raw_page_entity_url", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("raw_pages") as batch:
        batch.create_unique_constraint("uq_raw_page_entity_url", ["entity_id", "source_url"])
        batch.drop_column("raw_html_gz")
        batch.drop_column("http_status")
        batch.drop_column("http_last_modified")
        batch.drop_column("http_etag")
        batch.drop_column("content_sha256")
