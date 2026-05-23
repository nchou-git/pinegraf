from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_reconciliation_inference"
down_revision = "0009_audit_runs"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def _json_list_default() -> sa.TextClause:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return sa.text("'[]'::jsonb")
    return sa.text("'[]'")


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.add_column(sa.Column("connected_entity_id", sa.Uuid(), nullable=True))
        batch_op.add_column(
            sa.Column("is_inferred", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("derivation", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column(
                "source_ids",
                _json_type(),
                nullable=False,
                server_default=_json_list_default(),
            )
        )
        batch_op.alter_column(
            "source_raw_page_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.create_index("ix_connections_connected_entity_id", ["connected_entity_id"])
        batch_op.create_foreign_key(
            "fk_connections_connected_entity_id_entities",
            "entities",
            ["connected_entity_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "entity_consolidated",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("current_employer", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("current_title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("class_year", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_ids", _json_type(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id"),
    )


def downgrade() -> None:
    op.drop_table("entity_consolidated")
    op.execute("DELETE FROM connections WHERE is_inferred")
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint(
            "fk_connections_connected_entity_id_entities",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_connections_connected_entity_id")
        batch_op.alter_column(
            "source_raw_page_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.drop_column("source_ids")
        batch_op.drop_column("derivation")
        batch_op.drop_column("is_inferred")
        batch_op.drop_column("connected_entity_id")
