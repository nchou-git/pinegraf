"""add entity supersession for identity review

Revision ID: 0021_identity_review
Revises: 0020_entity_disambig
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0021_identity_review"
down_revision = "0020_entity_disambig"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("superseded_by_entity_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_entities_superseded_by_entity_id",
        "entities",
        "entities",
        ["superseded_by_entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_entities_superseded_by_entity_id",
        "entities",
        ["superseded_by_entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_entities_superseded_by_entity_id", table_name="entities")
    op.drop_constraint("fk_entities_superseded_by_entity_id", "entities", type_="foreignkey")
    op.drop_column("entities", "superseded_by_entity_id")
