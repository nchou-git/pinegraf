from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Entity, EntityAlias, EntityAttribute


def resolve_or_create(
    name: str,
    *,
    session: Session,
    context: dict[str, str] | None = None,
) -> uuid.UUID:
    """Resolve a name to an entity_id, creating a new entity if no
    confident match exists.

    Rules (deterministic, conservative; merge later via human review):
    1. If context['class_year'] is provided and exactly one entity has an
       alias matching name AND has a class_year attribute equal to that
       value -> return that entity_id.
    2. If context['current_company'] is provided and exactly one entity
       has an alias matching name AND has a current_company attribute
       matching -> return that entity_id.
    3. Otherwise, ALWAYS create a new entity. Do not collapse on name
       alone — that is the bug we are fixing.

    Alias matching is case-insensitive and whitespace-normalized.
    """
    context = context or {}
    canonical_name = _normalize_display_name(name)
    alias = _normalize_match_value(name)

    class_year = context.get("class_year")
    if class_year:
        matched = _entities_with_attribute(
            session=session,
            alias=alias,
            attribute_name="class_year",
            attribute_value=class_year,
        )
        if len(matched) == 1:
            return matched[0]

    current_company = context.get("current_company")
    if current_company:
        matched = _entities_with_attribute(
            session=session,
            alias=alias,
            attribute_name="current_company",
            attribute_value=current_company,
        )
        if len(matched) == 1:
            return matched[0]

    entity = Entity(
        entity_type="person",
        canonical_name=canonical_name,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(entity)
    session.flush()
    session.add(
        EntityAlias(
            entity_id=entity.id,
            alias=alias,
            source=context.get("source", "resolver"),
        )
    )
    _add_context_attribute(
        session,
        entity.id,
        "class_year",
        class_year,
        source=context.get("source", "resolver"),
    )
    _add_context_attribute(
        session,
        entity.id,
        "current_company",
        current_company,
        source=context.get("source", "resolver"),
    )
    session.flush()
    return entity.id


def _entities_with_attribute(
    *,
    session: Session,
    alias: str,
    attribute_name: str,
    attribute_value: str,
) -> list[uuid.UUID]:
    expected = _normalize_match_value(attribute_value)
    rows = session.execute(
        select(Entity.id, EntityAttribute.attribute_value)
        .join(EntityAlias, EntityAlias.entity_id == Entity.id)
        .join(EntityAttribute, EntityAttribute.entity_id == Entity.id)
        .where(
            EntityAlias.alias == alias,
            EntityAttribute.attribute_name == attribute_name,
            EntityAttribute.validation_verdict != "drop",
        )
    ).all()
    matched = {
        entity_id
        for entity_id, stored_value in rows
        if _normalize_match_value(stored_value) == expected
    }
    return list(matched)


def _add_context_attribute(
    session: Session,
    entity_id: uuid.UUID,
    attribute_name: str,
    attribute_value: str | None,
    *,
    source: str,
) -> None:
    cleaned = _normalize_display_name(attribute_value or "")
    if not cleaned:
        return
    session.add(
        EntityAttribute(
            entity_id=entity_id,
            attribute_name=attribute_name,
            attribute_value=cleaned,
            source=source,
            source_url=None,
            confidence="medium",
            extracted_at=datetime.now(UTC),
            validation_verdict="keep",
        )
    )


def _normalize_match_value(value: object) -> str:
    return _normalize_display_name(str(value)).lower()


def _normalize_display_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
