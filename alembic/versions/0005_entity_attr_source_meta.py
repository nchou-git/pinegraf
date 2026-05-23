from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_entity_attr_source_meta"
down_revision: str | None = "0004_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_ATTRIBUTE_NAMES = (
    "current_company",
    "current_title",
    "past_company",
    "education",
    "class_year",
    "bio_summary",
)
ATTRIBUTE_NAMES = (
    *LEGACY_ATTRIBUTE_NAMES,
    "internship_company",
    "internship_location",
    "current_employer",
    "current_employer_website",
    "current_location",
    "eship_notes",
)


def upgrade() -> None:
    with op.batch_alter_table("entity_attributes") as batch:
        batch.drop_constraint("ck_entity_attributes_attribute_name", type_="check")
        batch.add_column(
            sa.Column(
                "source",
                sa.String(length=255),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.add_column(sa.Column("as_of_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_entity_attributes_attribute_name",
            _attribute_name_check(ATTRIBUTE_NAMES),
        )
        batch.create_index("ix_entity_attributes_source", ["source"])

    op.execute(
        """
        UPDATE entity_attributes
        SET source = COALESCE(NULLIF(source_url, ''), 'legacy')
        """
    )


def downgrade() -> None:
    legacy_names = ", ".join(f"'{name}'" for name in LEGACY_ATTRIBUTE_NAMES)
    op.execute(f"DELETE FROM entity_attributes WHERE attribute_name NOT IN ({legacy_names})")
    with op.batch_alter_table("entity_attributes") as batch:
        batch.drop_index("ix_entity_attributes_source")
        batch.drop_constraint("ck_entity_attributes_attribute_name", type_="check")
        batch.create_check_constraint(
            "ck_entity_attributes_attribute_name",
            _attribute_name_check(LEGACY_ATTRIBUTE_NAMES),
        )
        batch.drop_column("last_verified_at")
        batch.drop_column("as_of_date")
        batch.drop_column("source")


def _attribute_name_check(attribute_names: Sequence[str]) -> str:
    quoted = ", ".join(f"'{name}'" for name in attribute_names)
    return f"attribute_name IN ({quoted})"
