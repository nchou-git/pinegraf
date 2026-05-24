from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select

from backend.db.models import Entity, EntityAlias, EntityMention
from backend.db.store import Store, utc_now
from backend.resolution.embedder import embed_text

ENTITY_OBJECT_TYPES = {"person", "org", "project", "place", "event"}
STOPWORDS = {"tuck", "dartmouth", "school", "business", "team", "company"}


@dataclass(frozen=True)
class Resolution:
    entity_id: uuid.UUID
    method: str
    confidence: float


async def resolve_mention(
    mention_text: str,
    kind: str,
    *,
    store: Store,
    context: str = "",
) -> Resolution | None:
    normalized = normalize_name(mention_text)
    if not normalized:
        return None
    exact = _exact_match(store, normalized, kind)
    if exact is not None:
        return exact
    alias = _fuzzy_alias_match(store, normalized, kind)
    if alias is not None and alias.confidence >= 0.85:
        return alias
    embedding = await _embedding_match(store, normalized, kind)
    if embedding is not None and embedding.confidence >= 0.82:
        return embedding
    if embedding is not None and 0.70 <= embedding.confidence < 0.82:
        if _llm_accepts(context, mention_text):
            return Resolution(embedding.entity_id, "llm", embedding.confidence)
    if looks_like_entity(mention_text):
        return await _create_entity(store, mention_text, kind)
    return None


def write_mention(
    *,
    store: Store,
    claim_raw_id: uuid.UUID,
    position: str,
    mention_text: str,
    resolution: Resolution,
) -> None:
    with store.session() as session:
        exists = session.execute(
            select(EntityMention).where(
                EntityMention.claim_raw_id == claim_raw_id,
                EntityMention.position == position,
            )
        ).scalar_one_or_none()
        if exists is not None:
            return
        session.add(
            EntityMention(
                claim_raw_id=claim_raw_id,
                position=position,
                entity_id=resolution.entity_id,
                mention_text=mention_text,
                resolution_method=resolution.method,
                resolution_confidence=resolution.confidence,
            )
        )
        session.commit()


def normalize_name(value: str | None) -> str:
    text = re.sub(r"\bT['’]?\d{2}\b", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def looks_like_entity(value: str) -> bool:
    words = [word for word in re.split(r"\s+", value.strip()) if word]
    if len(words) < 2:
        return False
    if normalize_name(value) in STOPWORDS:
        return False
    return any(word[:1].isupper() for word in words)


def _exact_match(store: Store, normalized: str, kind: str) -> Resolution | None:
    with store.session() as session:
        entities = list(session.execute(select(Entity).where(Entity.kind == kind)).scalars())
        for entity in entities:
            if normalize_name(entity.canonical_name) == normalized:
                return Resolution(entity.id, "exact_match", 1.0)
        aliases = list(
            session.execute(
                select(EntityAlias, Entity)
                .join(Entity, Entity.id == EntityAlias.entity_id)
                .where(Entity.kind == kind)
            ).all()
        )
        for alias, _entity in aliases:
            if normalize_name(alias.alias) == normalized:
                return Resolution(alias.entity_id, "exact_match", 1.0)
    return None


def _fuzzy_alias_match(store: Store, normalized: str, kind: str) -> Resolution | None:
    best: Resolution | None = None
    with store.session() as session:
        rows = list(
            session.execute(
                select(EntityAlias, Entity)
                .join(Entity, Entity.id == EntityAlias.entity_id)
                .where(Entity.kind == kind)
            ).all()
        )
    for alias, _entity in rows:
        score = SequenceMatcher(None, normalized, normalize_name(alias.alias)).ratio()
        if best is None or score > best.confidence:
            best = Resolution(alias.entity_id, "alias", score)
    return best


async def _embedding_match(store: Store, normalized: str, kind: str) -> Resolution | None:
    mention_embedding = await embed_text(normalized)
    best: Resolution | None = None
    with store.session() as session:
        rows = list(
            session.execute(
                select(Entity.id, Entity.embedding).where(
                    Entity.kind == kind,
                    Entity.embedding.is_not(None),
                )
            ).all()
        )
    for entity_id, embedding in rows:
        score = _cosine(mention_embedding, embedding or [])
        if best is None or score > best.confidence:
            best = Resolution(entity_id, "embedding", score)
    return best


async def _create_entity(store: Store, mention_text: str, kind: str) -> Resolution:
    embedding = await embed_text(mention_text)
    with store.session() as session:
        entity = Entity(
            kind=kind,
            canonical_name=mention_text.strip(),
            embedding=embedding,
            updated_at=utc_now(),
        )
        session.add(entity)
        session.flush()
        session.add(
            EntityAlias(
                entity_id=entity.id,
                alias=mention_text.strip(),
                confidence=1.0,
                source="resolution:new_entity",
            )
        )
        session.commit()
        return Resolution(entity.id, "new_entity", 0.75)


def _llm_accepts(context: str, mention_text: str) -> bool:
    del context, mention_text
    return False


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = sum(value * value for value in left[:size]) ** 0.5
    right_norm = sum(value * value for value in right[:size]) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
