"""add entity curation fields

Revision ID: 0022_entity_curation
Revises: 0021_identity_review
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_entity_curation"
down_revision = "0021_identity_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entities",
        sa.Column(
            "needs_human_disambiguation", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column("entities", sa.Column("verified_by", sa.Text(), nullable=True))
    op.add_column("entities", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "entities",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column("entities", sa.Column("merged_into_entity_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        "ck_entities_status",
        "entities",
        "status in ('active','archived','merged')",
    )
    op.create_foreign_key(
        "fk_entities_merged_into_entity_id",
        "entities",
        "entities",
        ["merged_into_entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_entities_needs_human_disambiguation",
        "entities",
        ["needs_human_disambiguation"],
    )
    op.create_index("ix_entities_status", "entities", ["status"])
    op.create_index(
        "ix_entities_merged_into_entity_id",
        "entities",
        ["merged_into_entity_id"],
    )
    op.alter_column("entities", "needs_human_disambiguation", server_default=None)
    op.alter_column("entities", "status", server_default=None)

    op.add_column("entity_disambiguation_candidates", sa.Column("mention_text", sa.Text()))
    op.add_column(
        "entity_disambiguation_candidates",
        sa.Column("context_chunk_id", sa.Uuid()),
    )
    op.create_foreign_key(
        "fk_entity_disambig_context_chunk_id",
        "entity_disambiguation_candidates",
        "chunks",
        ["context_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_entity_disambiguation_candidates_context_chunk_id",
        "entity_disambiguation_candidates",
        ["context_chunk_id"],
    )
    op.drop_constraint(
        "ck_entity_disambiguation_candidates_review_decision",
        "entity_disambiguation_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_entity_disambiguation_candidates_review_decision",
        "entity_disambiguation_candidates",
        "review_decision is null or review_decision in ('confirm','merge','split','defer')",
    )

    op.drop_constraint("ck_entity_mentions_resolution_method", "entity_mentions", type_="check")
    op.create_check_constraint(
        "ck_entity_mentions_resolution_method",
        "entity_mentions",
        "resolution_method in "
        "('exact_match','alias','embedding','llm','human','new_entity','strict_qualifier')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_entity_mentions_resolution_method", "entity_mentions", type_="check")
    op.create_check_constraint(
        "ck_entity_mentions_resolution_method",
        "entity_mentions",
        "resolution_method in ('exact_match','alias','embedding','llm','human','new_entity')",
    )
    op.drop_constraint(
        "ck_entity_disambiguation_candidates_review_decision",
        "entity_disambiguation_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_entity_disambiguation_candidates_review_decision",
        "entity_disambiguation_candidates",
        "review_decision is null or review_decision in ('confirm','merge','split')",
    )
    op.drop_index(
        "ix_entity_disambiguation_candidates_context_chunk_id",
        table_name="entity_disambiguation_candidates",
    )
    op.drop_constraint(
        "fk_entity_disambig_context_chunk_id",
        "entity_disambiguation_candidates",
        type_="foreignkey",
    )
    op.drop_column("entity_disambiguation_candidates", "context_chunk_id")
    op.drop_column("entity_disambiguation_candidates", "mention_text")

    op.drop_index("ix_entities_merged_into_entity_id", table_name="entities")
    op.drop_index("ix_entities_status", table_name="entities")
    op.drop_index("ix_entities_needs_human_disambiguation", table_name="entities")
    op.drop_constraint("fk_entities_merged_into_entity_id", "entities", type_="foreignkey")
    op.drop_constraint("ck_entities_status", "entities", type_="check")
    op.drop_column("entities", "merged_into_entity_id")
    op.drop_column("entities", "status")
    op.drop_column("entities", "verified_at")
    op.drop_column("entities", "verified_by")
    op.drop_column("entities", "needs_human_disambiguation")
