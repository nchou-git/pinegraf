from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Entity, EntityAlias, EntityAttribute
from backend.resolution.embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingClient,
    context_text,
    cosine_similarity,
)


def resolve_or_create(
    name: str,
    *,
    session: Session,
    context: dict[str, str] | None = None,
    embedding_client: EmbeddingClient | None = None,
    top_k: int = 10,
) -> uuid.UUID:
    """Resolve a candidate to an entity_id without merging on name alone."""
    context = context or {}
    embedding_client = embedding_client or DeterministicEmbeddingClient()
    canonical_name = _normalize_display_name(name)
    alias = _normalize_match_value(name)
    name_embedding = embedding_client.embed_text(
        canonical_name,
        purpose="entity_name_embedding",
    )
    context_embedding = embedding_client.embed_text(
        context_text(context),
        purpose="entity_context_embedding",
    )
    resolved = _resolve_by_embeddings(
        session=session,
        name_embedding=name_embedding,
        context_embedding=context_embedding,
        top_k=top_k,
    )
    if resolved is not None:
        _merge_entity_context(
            session=session,
            entity_id=resolved,
            alias=alias,
            context=context,
            source=context.get("source", "resolver"),
            name_embedding=name_embedding,
            context_embedding=context_embedding,
        )
        session.flush()
        return resolved

    class_year = context.get("class_year")
    current_company = context.get("current_company")
    entity = Entity(
        entity_type="person",
        canonical_name=canonical_name,
        name_embedding=name_embedding,
        context_embedding=context_embedding,
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


def _resolve_by_embeddings(
    *,
    session: Session,
    name_embedding: list[float],
    context_embedding: list[float],
    top_k: int,
) -> uuid.UUID | None:
    if not context_embedding or sum(abs(value) for value in context_embedding) == 0:
        return None
    rows = list(session.execute(select(Entity)).scalars())
    name_matches = [
        (entity, cosine_similarity(name_embedding, entity.name_embedding))
        for entity in rows
        if entity.name_embedding is not None
    ]
    name_matches = [
        (entity, similarity) for entity, similarity in name_matches if similarity > 0.85
    ]
    if not name_matches:
        return None
    candidates = sorted(name_matches, key=lambda item: item[1], reverse=True)[:top_k]
    context_matches = [
        (entity, cosine_similarity(context_embedding, entity.context_embedding))
        for entity, _name_similarity in candidates
        if entity.context_embedding is not None
    ]
    context_matches = [
        (entity, similarity) for entity, similarity in context_matches if similarity > 0.75
    ]
    if not context_matches:
        return None
    context_matches.sort(key=lambda item: item[1], reverse=True)
    best_entity, best_similarity = context_matches[0]
    tied = [
        entity
        for entity, similarity in context_matches
        if abs(similarity - best_similarity) < 0.000001
    ]
    if len(tied) != 1:
        return None
    return best_entity.id


def _merge_entity_context(
    *,
    session: Session,
    entity_id: uuid.UUID,
    alias: str,
    context: dict[str, str],
    source: str,
    name_embedding: list[float],
    context_embedding: list[float],
) -> None:
    entity = session.get(Entity, entity_id)
    if entity is None:
        return
    if entity.name_embedding is None:
        entity.name_embedding = name_embedding
    if entity.context_embedding is None:
        entity.context_embedding = context_embedding
    existing_alias = session.execute(
        select(EntityAlias.id).where(EntityAlias.entity_id == entity_id, EntityAlias.alias == alias)
    ).scalar_one_or_none()
    if existing_alias is None:
        session.add(EntityAlias(entity_id=entity_id, alias=alias, source=source))
    _add_context_attribute(
        session, entity_id, "class_year", context.get("class_year"), source=source
    )
    _add_context_attribute(
        session,
        entity_id,
        "current_company",
        context.get("current_company"),
        source=source,
    )


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
    existing = session.execute(
        select(EntityAttribute.id).where(
            EntityAttribute.entity_id == entity_id,
            EntityAttribute.attribute_name == attribute_name,
            EntityAttribute.attribute_value == cleaned,
            EntityAttribute.source == source,
        )
    ).scalar_one_or_none()
    if existing is not None:
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
