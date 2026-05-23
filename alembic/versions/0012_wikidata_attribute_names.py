from __future__ import annotations

from alembic import op

revision = "0012_wikidata_attribute_names"
down_revision = "0011_hybrid_retrieval"
branch_labels = None
depends_on = None

OLD_ATTRIBUTE_NAMES = (
    "'current_company', 'current_title', 'past_company', 'education', "
    "'class_year', 'bio_summary', 'internship_company', 'internship_location', "
    "'current_employer', 'current_employer_website', 'current_location', 'eship_notes'"
)
NEW_ATTRIBUTE_NAMES = (
    "'current_company', 'current_title', 'past_company', 'education', "
    "'class_year', 'bio_summary', 'internship_company', 'internship_location', "
    "'current_employer', 'current_employer_website', 'current_location', 'eship_notes', "
    "'date_of_birth', 'occupation', 'notable_for'"
)


def upgrade() -> None:
    _replace_attribute_check(NEW_ATTRIBUTE_NAMES)


def downgrade() -> None:
    op.execute(
        "DELETE FROM entity_attributes WHERE attribute_name IN "
        "('date_of_birth', 'occupation', 'notable_for')"
    )
    _replace_attribute_check(OLD_ATTRIBUTE_NAMES)


def _replace_attribute_check(attribute_names: str) -> None:
    with op.batch_alter_table("entity_attributes") as batch_op:
        batch_op.drop_constraint("ck_entity_attributes_attribute_name", type_="check")
        batch_op.create_check_constraint(
            "ck_entity_attributes_attribute_name",
            f"attribute_name IN ({attribute_names})",
        )
