from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_connection_project_targets"
down_revision = "0013_claims_subject_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.add_column(sa.Column("connected_project_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_connections_connected_project_id", ["connected_project_id"])
        batch_op.create_foreign_key(
            "fk_connections_connected_project_id_projects",
            "projects",
            ["connected_project_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint(
            "fk_connections_connected_project_id_projects",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_connections_connected_project_id")
        batch_op.drop_column("connected_project_id")
