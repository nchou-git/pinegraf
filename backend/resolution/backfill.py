from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import Entity, EntityAttribute
from backend.db.store import Store
from backend.resolution.embeddings import EmbeddingClient, context_text


@dataclass(frozen=True)
class BackfillSummary:
    entities_seen: int
    entities_updated: int


def backfill_entity_embeddings(
    store: Store,
    *,
    embedding_client: EmbeddingClient,
) -> BackfillSummary:
    with store.session() as session:
        entities = list(session.execute(select(Entity).order_by(Entity.created_at.asc())).scalars())
        updated = 0
        for entity in entities:
            context = _context_for_entity(session, entity)
            entity.name_embedding = embedding_client.embed_text(
                entity.canonical_name,
                purpose="entity_name_embedding",
                entity_id=entity.id,
            )
            entity.context_embedding = embedding_client.embed_text(
                context_text(context),
                purpose="entity_context_embedding",
                entity_id=entity.id,
            )
            updated += 1
        session.commit()
    return BackfillSummary(entities_seen=len(entities), entities_updated=updated)


def _context_for_entity(session: Session, entity: Entity) -> dict[str, object]:
    rows = list(
        session.execute(
            select(EntityAttribute.attribute_name, EntityAttribute.attribute_value).where(
                EntityAttribute.entity_id == entity.id,
                EntityAttribute.validation_verdict != "drop",
            )
        )
    )
    context: dict[str, object] = {}
    for attribute_name, attribute_value in rows:
        if attribute_name not in {
            "class_year",
            "current_company",
            "current_employer",
            "current_location",
            "current_title",
            "education",
        }:
            continue
        context.setdefault(attribute_name, attribute_value)
    return context
