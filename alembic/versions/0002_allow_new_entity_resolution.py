"""allow new_entity resolution method

Revision ID: 0002_new_entity_resolution
Revises: 0001_foundation
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op

revision = "0002_new_entity_resolution"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

NEW_CHECK = "resolution_method in ('exact_match','alias','embedding','llm','human','new_entity')"
OLD_CHECK = "resolution_method in ('exact_match','alias','embedding','llm','human')"


def upgrade() -> None:
    with op.batch_alter_table("entity_mentions") as batch_op:
        batch_op.drop_constraint("ck_entity_mentions_resolution_method", type_="check")
        batch_op.create_check_constraint("ck_entity_mentions_resolution_method", NEW_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("entity_mentions") as batch_op:
        batch_op.drop_constraint("ck_entity_mentions_resolution_method", type_="check")
        batch_op.create_check_constraint("ck_entity_mentions_resolution_method", OLD_CHECK)
