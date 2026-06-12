"""add entity disambiguation review candidates

Revision ID: 0020_entity_disambig
Revises: 0019_pipeline_reliability
Create Date: 2026-05-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_entity_disambig"
down_revision = "0019_pipeline_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_disambiguation_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        # Disambiguation runs before EntityMention is written, so mention_id is nullable.
        sa.Column("mention_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_entity_id", sa.Uuid(), nullable=False),
        sa.Column("llm_decision", sa.Text(), nullable=False),
        sa.Column("llm_reasoning", sa.Text(), nullable=True),
        sa.Column("name_similarity_score", sa.REAL(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_decision", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "llm_decision in ('merged','split','near_miss_review')",
            name="ck_entity_disambiguation_candidates_llm_decision",
        ),
        sa.CheckConstraint(
            "review_decision is null or review_decision in ('confirm','merge','split')",
            name="ck_entity_disambiguation_candidates_review_decision",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_entity_id"],
            ["entities.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mention_id"],
            ["entity_mentions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entity_disambiguation_candidates_mention_id",
        "entity_disambiguation_candidates",
        ["mention_id"],
    )
    op.create_index(
        "ix_entity_disambiguation_candidates_candidate_entity_id",
        "entity_disambiguation_candidates",
        ["candidate_entity_id"],
    )
    op.create_index(
        "ix_entity_disambiguation_candidates_created_at",
        "entity_disambiguation_candidates",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entity_disambiguation_candidates_created_at",
        table_name="entity_disambiguation_candidates",
    )
    op.drop_index(
        "ix_entity_disambiguation_candidates_candidate_entity_id",
        table_name="entity_disambiguation_candidates",
    )
    op.drop_index(
        "ix_entity_disambiguation_candidates_mention_id",
        table_name="entity_disambiguation_candidates",
    )
    op.drop_table("entity_disambiguation_candidates")
