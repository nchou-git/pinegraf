from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0002_entity_layer"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('person', 'organization')",
            name="ck_entities_entity_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "alias", name="uq_entity_alias_entity_alias"),
    )
    op.create_index("ix_entity_aliases_alias", "entity_aliases", ["alias"])
    op.create_index("ix_entity_aliases_entity_id", "entity_aliases", ["entity_id"])
    op.create_table(
        "entity_attributes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("attribute_name", sa.String(length=64), nullable=False),
        sa.Column("attribute_value", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validation_verdict", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "attribute_name IN ("
            "'current_company', 'current_title', 'past_company', 'education', "
            "'class_year', 'bio_summary'"
            ")",
            name="ck_entity_attributes_attribute_name",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_entity_attributes_confidence",
        ),
        sa.CheckConstraint(
            "validation_verdict IN ('keep', 'uncertain', 'drop')",
            name="ck_entity_attributes_validation_verdict",
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_attributes_entity_id", "entity_attributes", ["entity_id"])
    op.create_index(
        "ix_entity_attributes_entity_name",
        "entity_attributes",
        ["entity_id", "attribute_name"],
    )

    _add_entity_fk_columns()
    _backfill_entities()


def downgrade() -> None:
    _drop_entity_fk_columns()
    op.drop_index("ix_entity_attributes_entity_name", table_name="entity_attributes")
    op.drop_index("ix_entity_attributes_entity_id", table_name="entity_attributes")
    op.drop_table("entity_attributes")
    op.drop_index("ix_entity_aliases_entity_id", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_alias", table_name="entity_aliases")
    op.drop_table("entity_aliases")
    op.drop_table("entities")


def _add_entity_fk_columns() -> None:
    with op.batch_alter_table("raw_pages") as batch:
        batch.add_column(sa.Column("entity_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_raw_pages_entity_id", ["entity_id"])
        batch.create_foreign_key(
            "fk_raw_pages_entity_id_entities",
            "entities",
            ["entity_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint("uq_raw_page_alum_url", type_="unique")
        batch.create_unique_constraint("uq_raw_page_entity_url", ["entity_id", "source_url"])

    with op.batch_alter_table("alumni_profiles") as batch:
        batch.add_column(sa.Column("entity_id", sa.Uuid(), nullable=True))
        batch.create_index("ix_alumni_profiles_entity_id", ["entity_id"])
        batch.create_foreign_key(
            "fk_alumni_profiles_entity_id_entities",
            "entities",
            ["entity_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint("uq_alumni_profiles_name", type_="unique")
        batch.create_unique_constraint(
            "uq_alumni_profile_name_class",
            ["name", "class_year"],
        )

    for table_name in ("connections", "facts", "projects"):
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(sa.Column("entity_id", sa.Uuid(), nullable=True))
            batch.create_index(f"ix_{table_name}_entity_id", ["entity_id"])
            batch.create_foreign_key(
                f"fk_{table_name}_entity_id_entities",
                "entities",
                ["entity_id"],
                ["id"],
                ondelete="SET NULL",
            )


def _drop_entity_fk_columns() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("fk_projects_entity_id_entities", type_="foreignkey")
        batch.drop_index("ix_projects_entity_id")
        batch.drop_column("entity_id")
    with op.batch_alter_table("facts") as batch:
        batch.drop_constraint("fk_facts_entity_id_entities", type_="foreignkey")
        batch.drop_index("ix_facts_entity_id")
        batch.drop_column("entity_id")
    with op.batch_alter_table("connections") as batch:
        batch.drop_constraint("fk_connections_entity_id_entities", type_="foreignkey")
        batch.drop_index("ix_connections_entity_id")
        batch.drop_column("entity_id")

    with op.batch_alter_table("alumni_profiles") as batch:
        batch.drop_constraint("fk_alumni_profiles_entity_id_entities", type_="foreignkey")
        batch.drop_constraint("uq_alumni_profile_name_class", type_="unique")
        batch.drop_index("ix_alumni_profiles_entity_id")
        batch.drop_column("entity_id")
        batch.create_unique_constraint("uq_alumni_profiles_name", ["name"])

    with op.batch_alter_table("raw_pages") as batch:
        batch.drop_constraint("fk_raw_pages_entity_id_entities", type_="foreignkey")
        batch.drop_constraint("uq_raw_page_entity_url", type_="unique")
        batch.drop_index("ix_raw_pages_entity_id")
        batch.drop_column("entity_id")
        batch.create_unique_constraint("uq_raw_page_alum_url", ["alum_name", "source_url"])


def _backfill_entities() -> None:
    connection = op.get_bind()
    entities = sa.table(
        "entities",
        sa.column("id", sa.Uuid()),
        sa.column("entity_type", sa.String()),
        sa.column("canonical_name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    aliases = sa.table(
        "entity_aliases",
        sa.column("entity_id", sa.Uuid()),
        sa.column("alias", sa.String()),
        sa.column("source", sa.String()),
    )
    attributes = sa.table(
        "entity_attributes",
        sa.column("entity_id", sa.Uuid()),
        sa.column("attribute_name", sa.String()),
        sa.column("attribute_value", sa.Text()),
        sa.column("source_url", sa.String()),
        sa.column("confidence", sa.String()),
        sa.column("extracted_at", sa.DateTime(timezone=True)),
        sa.column("validation_verdict", sa.String()),
    )
    alumni_profiles = sa.table(
        "alumni_profiles",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("class_year", sa.String()),
        sa.column("current_company", sa.String()),
        sa.column("current_title", sa.String()),
        sa.column("past_companies", sa.JSON()),
        sa.column("education", sa.JSON()),
        sa.column("bio_summary", sa.Text()),
        sa.column("entity_id", sa.Uuid()),
    )
    raw_pages = sa.table(
        "raw_pages",
        sa.column("alum_name", sa.String()),
        sa.column("entity_id", sa.Uuid()),
    )
    connections = sa.table(
        "connections",
        sa.column("alum_name", sa.String()),
        sa.column("entity_id", sa.Uuid()),
    )
    facts = sa.table(
        "facts",
        sa.column("alum_name", sa.String()),
        sa.column("entity_id", sa.Uuid()),
    )
    projects = sa.table(
        "projects",
        sa.column("alum_name", sa.String()),
        sa.column("entity_id", sa.Uuid()),
    )

    profile_rows = list(
        connection.execute(
            sa.select(
                alumni_profiles.c.id,
                alumni_profiles.c.name,
                alumni_profiles.c.class_year,
                alumni_profiles.c.current_company,
                alumni_profiles.c.current_title,
                alumni_profiles.c.past_companies,
                alumni_profiles.c.education,
                alumni_profiles.c.bio_summary,
            ).order_by(alumni_profiles.c.id)
        ).mappings()
    )
    entity_by_key: dict[tuple[str, str], uuid.UUID] = {}
    entity_by_name: dict[str, uuid.UUID] = {}
    for row in profile_rows:
        name = str(row["name"] or "").strip()
        class_year = str(row["class_year"] or "").strip()
        if not name:
            continue
        key = (name, class_year)
        entity_id = entity_by_key.get(key)
        if entity_id is None:
            entity_id = uuid.uuid4()
            entity_by_key[key] = entity_id
            entity_by_name.setdefault(name, entity_id)
            now = datetime.now(UTC)
            connection.execute(
                entities.insert().values(
                    id=entity_id,
                    entity_type="person",
                    canonical_name=name,
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                aliases.insert().values(
                    entity_id=entity_id,
                    alias=_normalize_alias(name),
                    source="seed_csv",
                )
            )
            _insert_seed_attributes(connection, attributes, entity_id, row)
        connection.execute(
            alumni_profiles.update()
            .where(alumni_profiles.c.id == row["id"])
            .values(entity_id=entity_id)
        )

    for name, entity_id in entity_by_name.items():
        connection.execute(
            raw_pages.update().where(raw_pages.c.alum_name == name).values(entity_id=entity_id)
        )
        connection.execute(
            connections.update().where(connections.c.alum_name == name).values(entity_id=entity_id)
        )
        connection.execute(
            facts.update().where(facts.c.alum_name == name).values(entity_id=entity_id)
        )
        connection.execute(
            projects.update().where(projects.c.alum_name == name).values(entity_id=entity_id)
        )


def _insert_seed_attributes(
    connection: sa.Connection,
    attributes: sa.TableClause,
    entity_id: uuid.UUID,
    row: sa.RowMapping,
) -> None:
    now = datetime.now(UTC)
    scalar_fields = (
        ("current_company", row["current_company"]),
        ("current_title", row["current_title"]),
        ("bio_summary", row["bio_summary"]),
        ("class_year", row["class_year"]),
    )
    for attribute_name, value in scalar_fields:
        _insert_attribute(connection, attributes, entity_id, attribute_name, value, now)
    for company in _json_list(row["past_companies"]):
        _insert_attribute(connection, attributes, entity_id, "past_company", company, now)
    for education in _json_list(row["education"]):
        _insert_attribute(connection, attributes, entity_id, "education", education, now)


def _insert_attribute(
    connection: sa.Connection,
    attributes: sa.TableClause,
    entity_id: uuid.UUID,
    attribute_name: str,
    value: object,
    extracted_at: datetime,
) -> None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return
    connection.execute(
        attributes.insert().values(
            entity_id=entity_id,
            attribute_name=attribute_name,
            attribute_value=cleaned,
            source_url=None,
            confidence="medium",
            extracted_at=extracted_at,
            validation_verdict="keep",
        )
    )


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(loaded, list):
            return loaded
    return []


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()
