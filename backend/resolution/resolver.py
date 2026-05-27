from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import func, or_, select

from backend.class_year import CLASS_YEAR_RE, normalize_class_year
from backend.db.models import (
    AuditLog,
    Chunk,
    Claim,
    ClaimEvidence,
    ClaimRaw,
    Document,
    Entity,
    EntityAlias,
    EntityDisambiguationCandidate,
    EntityMention,
    EntitySummary,
)
from backend.db.store import Store, utc_now
from backend.resolution.embedder import embed_text
from backend.resolution.llm_disambiguator import EntityCandidate, ExtractedMention, disambiguate
from backend.util.vector import cosine, vector_values

ENTITY_OBJECT_TYPES = {"person", "org", "project", "place", "event"}
STOPWORDS = {"tuck", "dartmouth", "school", "business", "team", "company"}
AFFILIATION_KEYS = {"affiliated_with", "employed_by"}
QUALIFIER_PREDICATES = {
    "class_year",
    "affiliated_with",
    "employed_by",
    "located_in",
    "founded",
    "worked_on_project",
}


@dataclass(frozen=True)
class Resolution:
    entity_id: uuid.UUID
    method: str
    confidence: float
    reasoning: str | None = None


async def resolve_mention(
    mention_text: str,
    kind: str,
    *,
    store: Store,
    context_chunk: str = "",
) -> Resolution | None:
    mention_year = normalize_class_year(mention_text)
    normalized = normalize_name(mention_text)
    if not normalized:
        return None
    exact = _exact_match(store, normalized, kind, mention_year)
    if exact is not None:
        return exact
    alias = _fuzzy_alias_match(store, normalized, kind, mention_year)
    if alias is not None and alias.confidence >= 0.85:
        return alias
    embedding = await _embedding_match(store, normalized, kind, mention_year)
    if embedding is not None and embedding.confidence >= 0.85:
        block = _two_strike_block_for_entity(
            store,
            entity_id=embedding.entity_id,
            mention_year=mention_year,
            context_chunk=context_chunk,
            similarity=embedding.confidence,
        )
        if block is not None:
            _record_disambiguation_candidate(
                store,
                candidate=block["candidate"],
                decision="near_miss_review",
                reasoning=str(block["reasoning"]),
            )
            _record_resolution_audit(
                store,
                action="resolution.near_miss_review",
                target_id=embedding.entity_id,
                payload={
                    "mention": mention_text,
                    "confidence": embedding.confidence,
                    "reasoning": str(block["reasoning"]),
                },
            )
            return await _create_entity(store, mention_text, kind)
        _ensure_alias(store, embedding.entity_id, mention_text, source="resolution:embedding")
        return embedding
    if embedding is not None and 0.60 <= embedding.confidence < 0.85:
        llm = await _llm_match(
            store,
            mention_text=mention_text,
            kind=kind,
            normalized=normalized,
            mention_year=mention_year,
            context_chunk=context_chunk,
        )
        if llm is not None:
            if llm.method == "llm":
                _ensure_alias(store, llm.entity_id, mention_text, source="resolution:llm")
            return llm
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
    text = CLASS_YEAR_RE.sub("", value or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def looks_like_entity(value: str) -> bool:
    words = [word for word in re.split(r"\s+", value.strip()) if word]
    if len(words) < 2:
        return False
    if normalize_name(value) in STOPWORDS:
        return False
    return any(word[:1].isupper() for word in words)


def _exact_match(
    store: Store,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> Resolution | None:
    with store.session() as session:
        entities = list(session.execute(select(Entity).where(Entity.kind == kind)).scalars())
        for entity in entities:
            if normalize_name(entity.canonical_name) == normalized and _class_year_compatible(
                mention_year, _entity_class_year(session, entity)
            ):
                return Resolution(entity.id, "exact_match", 1.0)
        aliases = list(
            session.execute(
                select(EntityAlias, Entity)
                .join(Entity, Entity.id == EntityAlias.entity_id)
                .where(Entity.kind == kind)
            ).all()
        )
        for alias, entity in aliases:
            if normalize_name(alias.alias) == normalized and _class_year_compatible(
                mention_year, _entity_class_year(session, entity, alias.alias)
            ):
                return Resolution(alias.entity_id, "exact_match", 1.0)
    return None


def _fuzzy_alias_match(
    store: Store,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> Resolution | None:
    best: Resolution | None = None
    with store.session() as session:
        rows = list(
            session.execute(
                select(EntityAlias, Entity)
                .join(Entity, Entity.id == EntityAlias.entity_id)
                .where(Entity.kind == kind)
            ).all()
        )
        scored_rows = [
            (alias, entity, _entity_class_year(session, entity, alias.alias))
            for alias, entity in rows
        ]
    for alias, _entity, candidate_year in scored_rows:
        if not _class_year_compatible(mention_year, candidate_year):
            continue
        score = SequenceMatcher(None, normalized, normalize_name(alias.alias)).ratio()
        if mention_year is not None and mention_year == candidate_year:
            score = min(1.0, score + 0.05)
        if best is None or score > best.confidence:
            best = Resolution(alias.entity_id, "alias", score)
    return best


def _class_year_compatible(mention_year: int | None, candidate_year: int | None) -> bool:
    return mention_year is None or candidate_year is None or mention_year == candidate_year


def _entity_class_year(
    session,
    entity: Entity,
    alias_text: str | None = None,
) -> int | None:
    for value in (alias_text, entity.canonical_name):
        year = normalize_class_year(value)
        if year is not None:
            return year
    summary = session.get(EntitySummary, entity.id)
    if summary is None:
        return None
    primary = summary.primary_attributes or {}
    return normalize_class_year(str(primary.get("class_year", "")))


async def _embedding_match(
    store: Store,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> Resolution | None:
    mention_embedding = await embed_text(normalized)
    best: Resolution | None = None
    with store.session() as session:
        rows = list(
            session.execute(
                select(Entity).where(
                    Entity.kind == kind,
                    Entity.embedding.is_not(None),
                )
            ).all()
        )
        scored_rows = [(entity, _entity_class_year(session, entity)) for (entity,) in rows]
    for entity, candidate_year in scored_rows:
        score = cosine(mention_embedding, vector_values(entity.embedding))
        if mention_year is not None and mention_year == candidate_year:
            score = min(1.0, score + 0.05)
        if best is None or score > best.confidence:
            best = Resolution(entity.id, "embedding", score)
    return best


async def _llm_match(
    store: Store,
    *,
    mention_text: str,
    kind: str,
    normalized: str,
    mention_year: int | None,
    context_chunk: str,
) -> Resolution | None:
    candidates = await _ambiguous_candidates(
        store,
        normalized=normalized,
        kind=kind,
        mention_year=mention_year,
    )
    if not candidates:
        return None
    mention_qualifiers = _mention_qualifiers(mention_text, context_chunk)
    block = _two_strike_block(
        candidates[0],
        mention_year,
        context_chunk,
        mention_qualifiers,
    )
    if block is not None:
        _record_disambiguation_candidate(
            store,
            candidate=candidates[0],
            decision="near_miss_review",
            reasoning=block,
        )
        _record_resolution_audit(
            store,
            action="resolution.near_miss_review",
            target_id=candidates[0].entity_id,
            payload={
                "mention": mention_text,
                "confidence": candidates[0].similarity,
                "reasoning": block,
            },
        )
        return await _create_entity(store, mention_text, kind)
    result = await disambiguate(
        ExtractedMention(
            text=mention_text,
            type=kind,
            qualifiers=mention_qualifiers,
        ),
        candidates,
        context_chunk,
    )
    if result.entity_id is None:
        top = candidates[0]
        decision = "near_miss_review" if top.similarity >= 0.70 else "split"
        _record_disambiguation_candidate(
            store,
            candidate=top,
            decision=decision,
            reasoning=result.reasoning,
        )
        _record_resolution_audit(
            store,
            action="resolution.llm_new_entity",
            target_id=mention_text,
            payload={"confidence": result.confidence, "reasoning": result.reasoning},
        )
        return await _create_entity(store, mention_text, kind)
    matched = next(
        (candidate for candidate in candidates if candidate.entity_id == result.entity_id),
        None,
    )
    if matched is not None:
        _record_disambiguation_candidate(
            store,
            candidate=matched,
            decision="merged",
            reasoning=result.reasoning,
        )
    _record_resolution_audit(
        store,
        action="resolution.llm_match",
        target_id=result.entity_id,
        payload={
            "mention": mention_text,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
        },
    )
    return Resolution(result.entity_id, "llm", result.confidence, result.reasoning)


async def _ambiguous_candidates(
    store: Store,
    *,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> list[EntityCandidate]:
    del mention_year
    mention_embedding = await embed_text(normalized)
    rows: list[tuple[Entity, list[str], dict[str, list[str]], int, object | None]] = []
    with store.session() as session:
        entities = list(
            session.execute(
                select(Entity).where(Entity.kind == kind, Entity.embedding.is_not(None))
            ).scalars()
        )
        for entity in entities:
            aliases = list(
                session.execute(
                    select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
                ).scalars()
            )
            qualifiers = _candidate_qualifiers(session, entity)
            document_count, last_seen_at = _candidate_document_stats(session, entity)
            rows.append((entity, aliases, qualifiers, document_count, last_seen_at))
    candidates: list[EntityCandidate] = []
    for entity, aliases, qualifiers, document_count, last_seen_at in rows:
        score = cosine(mention_embedding, vector_values(entity.embedding))
        if 0.60 <= score < 0.85:
            candidates.append(
                EntityCandidate(
                    entity_id=entity.id,
                    name=entity.canonical_name,
                    kind=entity.kind,
                    aliases=aliases,
                    document_count=document_count,
                    last_seen_at=last_seen_at,
                    similarity=score,
                    qualifiers=qualifiers,
                )
            )
    return sorted(candidates, key=lambda item: item.similarity, reverse=True)[:5]


def _candidate_qualifiers(session, entity: Entity) -> dict[str, list[str]]:
    qualifiers: dict[str, list[str]] = {}
    summary = session.get(EntitySummary, entity.id)
    if summary is not None and summary.primary_attributes:
        class_year = summary.primary_attributes.get("class_year")
        if class_year is not None:
            qualifiers.setdefault("class_year", []).append(str(class_year))
    for claim, other in session.execute(
        select(Claim, Entity)
        .outerjoin(Entity, Entity.id == Claim.object_entity_id)
        .where(
            Claim.predicate.in_(QUALIFIER_PREDICATES),
            or_(Claim.subject_entity_id == entity.id, Claim.object_entity_id == entity.id),
        )
        .limit(25)
    ).all():
        if claim.subject_entity_id == entity.id:
            value = other.canonical_name if other is not None else claim.object_value
            key = claim.predicate
        else:
            subject = session.get(Entity, claim.subject_entity_id)
            value = subject.canonical_name if subject is not None else str(claim.subject_entity_id)
            key = f"object_of_{claim.predicate}"
        if value is not None:
            qualifiers.setdefault(key, [])
            if str(value) not in qualifiers[key]:
                qualifiers[key].append(str(value))
    year = _entity_class_year(session, entity)
    if year is not None and str(year) not in qualifiers.setdefault("class_year", []):
        qualifiers["class_year"].append(str(year))
    return {key: values[:5] for key, values in qualifiers.items() if values}


def _candidate_document_stats(session, entity: Entity) -> tuple[int, object | None]:
    row = session.execute(
        select(func.count(func.distinct(Document.id)), func.max(Chunk.created_at))
        .select_from(EntityMention)
        .join(ClaimRaw, ClaimRaw.id == EntityMention.claim_raw_id)
        .join(ClaimEvidence, ClaimEvidence.claim_raw_id == ClaimRaw.id)
        .join(Chunk, Chunk.id == ClaimRaw.chunk_id)
        .join(Document, Document.id == Chunk.document_id)
        .where(EntityMention.entity_id == entity.id)
    ).one()
    return int(row[0] or 0), row[1]


def _mention_qualifiers(mention_text: str, context_chunk: str) -> dict[str, list[str]]:
    qualifiers: dict[str, list[str]] = {}
    text = f"{mention_text} {context_chunk}"
    year = normalize_class_year(text)
    if year is not None:
        qualifiers["class_year"] = [str(year)]
    for key, pattern in (
        (
            "employed_by",
            r"\b(?:works at|worked at|joined|employed by|employee of)\s+"
            r"(?P<value>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,5})",
        ),
        (
            "affiliated_with",
            r"\b(?:affiliated with|member of|faculty at|student at)\s+"
            r"(?P<value>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,5})",
        ),
        (
            "located_in",
            r"\b(?:located in|based in|lives in|from)\s+"
            r"(?P<value>[A-Z][A-Za-z0-9&'.-]+(?:\s+[A-Z][A-Za-z0-9&'.-]+){0,5})",
        ),
    ):
        values = [
            _clean_qualifier_value(match.group("value")) for match in re.finditer(pattern, text)
        ]
        values = [value for value in values if value]
        if values:
            qualifiers[key] = _dedupe(values)
    return qualifiers


def _clean_qualifier_value(value: str) -> str:
    return re.split(
        r"[.;,]|\s+(?:and|before|after|while|where|who)\b",
        value.strip(),
        maxsplit=1,
    )[0].strip()


def _dedupe(values: list[str]) -> list[str]:
    output = []
    seen = set()
    for value in values:
        normalized = normalize_name(value)
        if normalized and normalized not in seen:
            output.append(value)
            seen.add(normalized)
    return output


def _two_strike_block_for_entity(
    store: Store,
    *,
    entity_id: uuid.UUID,
    mention_year: int | None,
    context_chunk: str,
    similarity: float,
) -> dict[str, object] | None:
    with store.session() as session:
        entity = session.get(Entity, entity_id)
        if entity is None:
            return None
        candidate = EntityCandidate(
            entity_id=entity.id,
            name=entity.canonical_name,
            kind=entity.kind,
            aliases=list(
                session.execute(
                    select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
                ).scalars()
            ),
            document_count=0,
            last_seen_at=None,
            similarity=similarity,
            qualifiers=_candidate_qualifiers(session, entity),
        )
    reasoning = _two_strike_block(
        candidate,
        mention_year,
        context_chunk,
        _mention_qualifiers("", context_chunk),
    )
    if reasoning is None:
        return None
    return {"candidate": candidate, "reasoning": reasoning}


def _two_strike_block(
    candidate: EntityCandidate,
    mention_year: int | None,
    context_chunk: str,
    mention_qualifiers: dict[str, list[str]] | None = None,
) -> str | None:
    mention_qualifiers = mention_qualifiers or {}
    candidate_years = {
        normalize_class_year(value)
        for value in candidate.qualifiers.get("class_year", [])
        if normalize_class_year(value) is not None
    }
    if mention_year is not None and candidate_years and mention_year not in candidate_years:
        return (
            f"near miss: mention class year {mention_year} conflicts with candidate "
            f"class_year {sorted(candidate_years)}"
        )
    affiliation_reason = _qualifier_conflict_reason(
        mention_qualifiers,
        candidate.qualifiers,
        mention_keys=AFFILIATION_KEYS,
        candidate_keys=AFFILIATION_KEYS,
        label="affiliation",
    )
    if affiliation_reason is not None:
        return affiliation_reason
    location_reason = _qualifier_conflict_reason(
        mention_qualifiers,
        candidate.qualifiers,
        mention_keys={"located_in"},
        candidate_keys={"located_in"},
        label="location",
    )
    if location_reason is not None:
        return location_reason
    mention_has_qualifiers = (
        mention_year is not None
        or _context_has_strong_alignment(context_chunk, candidate)
        or any(
            mention_qualifiers.get(key) for key in ("affiliated_with", "employed_by", "located_in")
        )
    )
    if not mention_has_qualifiers and candidate.qualifiers and candidate.similarity < 0.78:
        return (
            "near miss: mention has no qualifiers while candidate has qualifiers; "
            "insufficient context for a safe merge"
        )
    return None


def _qualifier_conflict_reason(
    mention_qualifiers: dict[str, list[str]],
    candidate_qualifiers: dict[str, list[str]],
    *,
    mention_keys: set[str],
    candidate_keys: set[str],
    label: str,
) -> str | None:
    mention_values = _normalized_qualifier_values(mention_qualifiers, mention_keys)
    candidate_values = _normalized_qualifier_values(candidate_qualifiers, candidate_keys)
    if not mention_values or not candidate_values:
        return None
    if set(mention_values).isdisjoint(set(candidate_values)):
        return (
            f"near miss: mention {label} {sorted(mention_values.values())} conflicts "
            f"with candidate {label} {sorted(candidate_values.values())}"
        )
    return None


def _normalized_qualifier_values(
    qualifiers: dict[str, list[str]],
    keys: set[str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in keys:
        for value in qualifiers.get(key, []):
            normalized = normalize_name(value)
            if normalized:
                values[normalized] = value
    return values


def _context_has_strong_alignment(context_chunk: str, candidate: EntityCandidate) -> bool:
    normalized_context = normalize_name(context_chunk)
    for values in candidate.qualifiers.values():
        for value in values:
            if normalize_name(value) and normalize_name(value) in normalized_context:
                return True
    return False


def _record_disambiguation_candidate(
    store: Store,
    *,
    candidate: EntityCandidate,
    decision: str,
    reasoning: str,
) -> None:
    with store.session() as session:
        session.add(
            EntityDisambiguationCandidate(
                mention_id=None,
                candidate_entity_id=candidate.entity_id,
                llm_decision=decision,
                llm_reasoning=reasoning,
                name_similarity_score=candidate.similarity,
            )
        )
        session.commit()


def _ensure_alias(store: Store, entity_id: uuid.UUID, mention_text: str, *, source: str) -> None:
    alias = mention_text.strip()
    if not alias:
        return
    with store.session() as session:
        entity = session.get(Entity, entity_id)
        if entity is None or normalize_name(entity.canonical_name) == normalize_name(alias):
            return
        exists = session.execute(
            select(EntityAlias).where(
                EntityAlias.entity_id == entity_id,
                func.lower(EntityAlias.alias) == alias.casefold(),
            )
        ).scalar_one_or_none()
        if exists is not None:
            return
        session.add(EntityAlias(entity_id=entity_id, alias=alias, confidence=0.9, source=source))
        session.add(
            AuditLog(
                action="resolution.alias_created",
                target_table="entities",
                target_id=str(entity_id),
                actor="system",
                payload={"alias": alias, "source": source},
            )
        )
        session.commit()


def _record_resolution_audit(
    store: Store,
    *,
    action: str,
    target_id: uuid.UUID | str,
    payload: dict[str, object],
) -> None:
    with store.session() as session:
        session.add(
            AuditLog(
                action=action,
                target_table="entities",
                target_id=str(target_id),
                actor="system",
                payload=payload,
            )
        )
        session.commit()


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
