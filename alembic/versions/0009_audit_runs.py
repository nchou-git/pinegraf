from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_audit_runs"
down_revision = "0008_entity_embeddings_pgvector"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    json_type = _json_type()
    op.create_table(
        "audit_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("thrifty_results", json_type, nullable=False),
        sa.Column("frontier_results", json_type, nullable=False),
        sa.Column("diff_summary", json_type, nullable=False),
    )
    op.create_index("ix_audit_runs_run_at", "audit_runs", ["run_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_runs_run_at", table_name="audit_runs")
    op.drop_table("audit_runs")
