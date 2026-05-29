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
from backend.extraction.extractor import is_structurally_valid_name
from backend.resolution.embedder import embed_text
from backend.resolution.llm_disambiguator import EntityCandidate, ExtractedMention, disambiguate

ENTITY_OBJECT_TYPES = {"person", "org", "project", "place", "event"}
GENERIC_TITLE_PATTERN = re.compile(r"^the\s+(?:dean|professor|director|chair|lecturer)\b", re.I)
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
    review_candidate_id: uuid.UUID | None = None


async def resolve_mention(
    mention_text: str,
    kind: str,
    *,
    store: Store,
    context_chunk: str = "",
    context_chunk_id: uuid.UUID | None = None,
) -> Resolution | None:
    mention_year = normalize_class_year(mention_text)
    normalized = normalize_name_strict(mention_text)
    if not normalized:
        return None

    if _is_low_signal_mention(mention_text, kind):
        resolution = await _create_entity(
            store,
            mention_text,
            kind,
            needs_human_disambiguation=True,
        )
        review = _queue_review_candidate_for_entity(
            store,
            mention_text=mention_text,
            context_chunk_id=context_chunk_id,
            entity_id=resolution.entity_id,
            decision="near_miss_review",
            reasoning="low-signal mention skipped deterministic matching",
            similarity=0.0,
        )
        _record_resolution_audit(
            store,
            action="resolution.low_signal_skip",
            target_id=resolution.entity_id,
            payload={"mention": mention_text, "kind": kind},
        )
        return Resolution(
            resolution.entity_id,
            resolution.method,
            resolution.confidence,
            resolution.reasoning,
            review_candidate_id=review,
        )

    exact = _strict_exact_match(store, normalized, kind, mention_year)
    if exact is not None:
        return exact

    mention_qualifiers = _mention_qualifiers(mention_text, context_chunk)
    strict = _strict_qualifier_match(
        store,
        normalized=normalized,
        kind=kind,
        mention_year=mention_year,
        mention_qualifiers=mention_qualifiers,
    )
    if strict is not None:
        return strict

    candidates = _name_candidates(
        store,
        normalized=normalized,
        kind=kind,
        mention_year=mention_year,
    )
    top = candidates[0] if candidates else None
    if top is not None and top.similarity >= 0.80:
        result = await disambiguate(
            ExtractedMention(
                text=mention_text,
                type=kind,
                qualifiers=mention_qualifiers,
            ),
            candidates[:5],
            context_chunk,
        )
        matched = next(
            (candidate for candidate in candidates if candidate.entity_id == result.entity_id),
            None,
        )
        if matched is not None:
            candidate_qualifiers = matched.qualifiers
            threshold = 0.70 if matched.verified_by else 0.85
            if (
                result.confidence >= threshold
                and _qualifiers_corroborate(mention_qualifiers, candidate_qualifiers)
            ):
                _ensure_alias(store, matched.entity_id, mention_text, source="resolution:llm")
                _record_resolution_audit(
                    store,
                    action="resolution.llm_merged",
                    target_id=matched.entity_id,
                    payload={
                        "mention": mention_text,
                        "confidence": result.confidence,
                        "reasoning": result.reasoning,
                        "verified_candidate": bool(matched.verified_by),
                    },
                )
                return Resolution(matched.entity_id, "llm", result.confidence, result.reasoning)
        review = _queue_review_candidate(
            store,
            mention_text=mention_text,
            context_chunk_id=context_chunk_id,
            candidate=top,
            decision="near_miss_review",
            reasoning=result.reasoning,
            source_entity_id=None,
        )
        entity = await _create_entity(store, mention_text, kind)
        _record_resolution_audit(
            store,
            action="resolution.review_queued",
            target_id=entity.entity_id,
            payload={
                "mention": mention_text,
                "candidate_entity_id": str(top.entity_id),
                "review_candidate_id": str(review),
            },
        )
        return Resolution(
            entity.entity_id,
            entity.method,
            entity.confidence,
            result.reasoning,
            review_candidate_id=review,
        )

    if top is not None:
        review = _queue_review_candidate(
            store,
            mention_text=mention_text,
            context_chunk_id=context_chunk_id,
            candidate=top,
            decision="near_miss_review",
            reasoning="advisory candidate below LLM threshold",
            source_entity_id=None,
        )
        entity = await _create_entity(store, mention_text, kind)
        _record_resolution_audit(
            store,
            action="resolution.review_queued",
            target_id=entity.entity_id,
            payload={
                "mention": mention_text,
                "candidate_entity_id": str(top.entity_id),
                "review_candidate_id": str(review),
            },
        )
        return Resolution(
            entity.entity_id,
            entity.method,
            entity.confidence,
            review_candidate_id=review,
        )

    return await _create_entity(store, mention_text, kind)


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
        mention = EntityMention(
            claim_raw_id=claim_raw_id,
            position=position,
            entity_id=resolution.entity_id,
            mention_text=mention_text,
            resolution_method=resolution.method,
            resolution_confidence=resolution.confidence,
        )
        session.add(mention)
        session.flush()
        if resolution.review_candidate_id is not None:
            candidate = session.get(EntityDisambiguationCandidate, resolution.review_candidate_id)
            if candidate is not None:
                candidate.mention_id = mention.id
        session.commit()


def normalize_name(value: str | None) -> str:
    text = CLASS_YEAR_RE.sub("", value or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def normalize_name_strict(value: str | None) -> str:
    text = CLASS_YEAR_RE.sub("", value or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    for prefix in ("mr ", "mrs ", "ms ", "dr ", "prof ", "professor "):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    for suffix in (" jr", " sr", " ii", " iii", " iv", " phd", " md", " mba"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip()


def _is_low_signal_mention(mention_text: str, kind: str) -> bool:
    normalized_text = normalize_name_strict(mention_text)
    tokens = [token for token in normalized_text.split() if token]
    structural_text = re.sub(r"\s+", " ", CLASS_YEAR_RE.sub("", mention_text)).strip()
    if kind == "person" and len(tokens) < 2:
        return True
    if kind == "person" and not is_structurally_valid_name(structural_text, "person"):
        return True
    return kind == "person" and bool(GENERIC_TITLE_PATTERN.match(mention_text.strip()))


def _strict_exact_match(
    store: Store,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> Resolution | None:
    with store.session() as session:
        entities = list(
            session.execute(
                select(Entity).where(Entity.kind == kind, Entity.status == "active")
            ).scalars()
        )
        for entity in entities:
            name_matches = normalize_name_strict(entity.canonical_name) == normalized
            year_matches = _class_year_compatible(
                mention_year,
                _entity_class_year(session, entity),
            )
            if name_matches and year_matches:
                return Resolution(entity.id, "exact_match", 1.0)
        aliases = list(
            session.execute(
                select(EntityAlias, Entity)
                .join(Entity, Entity.id == EntityAlias.entity_id)
                .where(Entity.kind == kind, Entity.status == "active")
            ).all()
        )
        for alias, entity in aliases:
            if normalize_name_strict(alias.alias) == normalized and _class_year_compatible(
                mention_year, _entity_class_year(session, entity, alias.alias)
            ):
                return Resolution(alias.entity_id, "exact_match", 1.0)
    return None


def _strict_qualifier_match(
    store: Store,
    *,
    normalized: str,
    kind: str,
    mention_year: int | None,
    mention_qualifiers: dict[str, list[str]],
) -> Resolution | None:
    if not any(mention_qualifiers.values()):
        return None
    best: tuple[Entity, float, dict[str, list[str]]] | None = None
    with store.session() as session:
        entities = list(
            session.execute(
                select(Entity).where(Entity.kind == kind, Entity.status == "active")
            ).scalars()
        )
        for entity in entities:
            candidate_year = _entity_class_year(session, entity)
            if not _class_year_compatible(mention_year, candidate_year):
                continue
            names = [entity.canonical_name, *list(
                session.execute(
                    select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
                ).scalars()
            )]
            score = max(
                SequenceMatcher(None, normalized, normalize_name_strict(name)).ratio()
                for name in names
            )
            qualifiers = _candidate_qualifiers(session, entity)
            if score >= 0.92 and _qualifiers_corroborate(mention_qualifiers, qualifiers):
                if best is None or score > best[1]:
                    best = (entity, score, qualifiers)
    if best is None:
        return None
    return Resolution(best[0].id, "strict_qualifier", best[1])


def _class_year_compatible(mention_year: int | None, candidate_year: int | None) -> bool:
    return mention_year is None or candidate_year is None or mention_year == candidate_year


def _name_candidates(
    store: Store,
    *,
    normalized: str,
    kind: str,
    mention_year: int | None,
) -> list[EntityCandidate]:
    candidates: list[EntityCandidate] = []
    with store.session() as session:
        entities = list(
            session.execute(
                select(Entity).where(Entity.kind == kind, Entity.status == "active")
            ).scalars()
        )
        for entity in entities:
            candidate_year = _entity_class_year(session, entity)
            if not _class_year_compatible(mention_year, candidate_year):
                continue
            aliases = list(
                session.execute(
                    select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
                ).scalars()
            )
            names = [entity.canonical_name, *aliases]
            name_score = max(
                SequenceMatcher(None, normalized, normalize_name_strict(name)).ratio()
                for name in names
            )
            score = name_score
            if score < 0.60:
                continue
            qualifiers = _candidate_qualifiers(session, entity)
            document_count, last_seen_at = _candidate_document_stats(session, entity)
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
                    verified_by=entity.verified_by,
                    recent_claims=_recent_claim_summaries(session, entity.id),
                )
            )
    return sorted(candidates, key=lambda item: item.similarity, reverse=True)[:10]


def _qualifiers_corroborate(
    mention: dict[str, list[str]],
    candidate: dict[str, list[str]],
) -> bool:
    if not any(mention.values()):
        return True
    matched = False
    for key, mention_values in mention.items():
        if not mention_values:
            continue
        candidate_values = candidate.get(key, [])
        if not candidate_values:
            continue
        mention_set = {value.casefold() for value in mention_values}
        candidate_set = {value.casefold() for value in candidate_values}
        if mention_set & candidate_set:
            matched = True
        else:
            return False
    return matched


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


def _recent_claim_summaries(session, entity_id: uuid.UUID) -> list[str]:
    rows = session.execute(
        select(Claim, Entity)
        .outerjoin(Entity, Entity.id == Claim.object_entity_id)
        .where(or_(Claim.subject_entity_id == entity_id, Claim.object_entity_id == entity_id))
        .order_by(Claim.last_corroborated_at.desc())
        .limit(10)
    ).all()
    summaries: list[str] = []
    for claim, object_entity in rows:
        if claim.subject_entity_id == entity_id:
            value = object_entity.canonical_name if object_entity else claim.object_value
            summaries.append(f"{claim.predicate}: {value or 'unknown'}")
        else:
            subject = session.get(Entity, claim.subject_entity_id)
            summaries.append(
                f"object_of_{claim.predicate}: "
                f"{subject.canonical_name if subject else claim.subject_entity_id}"
            )
    return summaries


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


def _queue_review_candidate(
    store: Store,
    *,
    mention_text: str,
    context_chunk_id: uuid.UUID | None,
    candidate: EntityCandidate,
    decision: str,
    reasoning: str,
    source_entity_id: uuid.UUID | None,
) -> uuid.UUID:
    del source_entity_id
    with store.session() as session:
        row = EntityDisambiguationCandidate(
            mention_id=None,
            mention_text=mention_text,
            context_chunk_id=context_chunk_id,
            candidate_entity_id=candidate.entity_id,
            llm_decision=decision,
            llm_reasoning=reasoning,
            name_similarity_score=candidate.similarity,
        )
        session.add(row)
        session.commit()
        return row.id


def _queue_review_candidate_for_entity(
    store: Store,
    *,
    mention_text: str,
    context_chunk_id: uuid.UUID | None,
    entity_id: uuid.UUID,
    decision: str,
    reasoning: str,
    similarity: float,
) -> uuid.UUID:
    with store.session() as session:
        row = EntityDisambiguationCandidate(
            mention_id=None,
            mention_text=mention_text,
            context_chunk_id=context_chunk_id,
            candidate_entity_id=entity_id,
            llm_decision=decision,
            llm_reasoning=reasoning,
            name_similarity_score=similarity,
        )
        session.add(row)
        session.commit()
        return row.id


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


async def _create_entity(
    store: Store,
    mention_text: str,
    kind: str,
    *,
    needs_human_disambiguation: bool = False,
) -> Resolution:
    embedding = await embed_text(mention_text)
    with store.session() as session:
        entity = Entity(
            kind=kind,
            canonical_name=mention_text.strip(),
            embedding=embedding,
            needs_human_disambiguation=needs_human_disambiguation,
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
