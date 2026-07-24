from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from backend.db.models import (
    Chunk,
    Claim,
    ClaimEvidence,
    ClaimRaw,
    Document,
    DocumentFetch,
    Entity,
    EntityAlias,
    EntityMention,
    EntityNeighborhood,
    EntitySummary,
    ExtractorRun,
)
from backend.db.store import Store

TEST_SOURCE_IDENTIFIER = "test-demo.local"
TEST_SOURCE_DISPLAY_NAME = "TEST — Synthetic demo source [DEMO]"
TEST_DOCUMENT_URL = "https://test-demo.local/coffee-graph-demo"


@dataclass(frozen=True)
class SeedEntity:
    key: str
    name: str
    kind: str
    attributes: dict[str, object]


@dataclass(frozen=True)
class SeedClaim:
    subject: str
    predicate: str
    object: str
    is_resolved: bool = True


ENTITIES = [
    SeedEntity(
        "ava",
        "TEST — Ava Example [DEMO]",
        "person",
        {
            "current_title": "TEST — Demo fellow",
            "current_employer": "TEST — Bytebrew Labs [DEMO]",
            "class_year": "TEST — 2099",
        },
    ),
    SeedEntity(
        "milo",
        "TEST — Milo Testerton [DEMO]",
        "person",
        {"current_title": "TEST — Prototype analyst", "class_year": "TEST — 2100"},
    ),
    SeedEntity(
        "priya",
        "TEST — Priya Demo [DEMO]",
        "person",
        {"current_title": "TEST — Student operator", "class_year": "TEST — 2101"},
    ),
    SeedEntity(
        "omar",
        "TEST — Omar Sample [DEMO]",
        "person",
        {"current_title": "TEST — Visiting mentor", "class_year": "TEST — 2098"},
    ),
    SeedEntity(
        "bytebrew",
        "TEST — Bytebrew Labs [DEMO]",
        "org",
        {"sector": "TEST — Synthetic coffee analytics"},
    ),
    SeedEntity(
        "cuppa",
        "TEST — Cuppa Analytics [DEMO]",
        "org",
        {"sector": "TEST — Synthetic data tools"},
    ),
    SeedEntity(
        "university",
        "TEST — Demo University [DEMO]",
        "org",
        {"sector": "TEST — Synthetic education"},
    ),
    SeedEntity(
        "pinecone",
        "TEST — Pinecone Scholarship Project [DEMO]",
        "project",
        {"theme": "TEST — Synthetic scholarships"},
    ),
    SeedEntity(
        "sprint",
        "TEST — Espresso Mentorship Sprint [DEMO]",
        "project",
        {"theme": "TEST — Synthetic mentorship"},
    ),
    SeedEntity(
        "forum",
        "TEST — Founders Forum 2099 [DEMO]",
        "event",
        {"theme": "TEST — Synthetic founder talks"},
    ),
]

CLAIMS = [
    SeedClaim("ava", "works_at", "bytebrew"),
    SeedClaim("ava", "contributes_to", "pinecone"),
    SeedClaim("bytebrew", "sponsors", "pinecone"),
    SeedClaim("milo", "works_at", "cuppa"),
    SeedClaim("priya", "studies_at", "university"),
    SeedClaim("omar", "mentors", "ava", is_resolved=False),
    SeedClaim("priya", "collaborates_with", "milo"),
    SeedClaim("cuppa", "hosts", "forum"),
    SeedClaim("forum", "features", "pinecone", is_resolved=False),
    SeedClaim("university", "partners_with", "bytebrew"),
    SeedClaim("omar", "advises", "sprint"),
    SeedClaim("sprint", "supports", "pinecone"),
    SeedClaim("milo", "contributes_to", "sprint"),
]


def seed_synthetic_test_data(store: Store) -> dict[str, int]:
    source = store.upsert_source(
        kind="domain",
        identifier=TEST_SOURCE_IDENTIFIER,
        trust_weight=0.8,
        respect_robots=True,
        display_name=TEST_SOURCE_DISPLAY_NAME,
        notes="Obvious fake test data for the public Coffee Graph demo.",
    )
    if _seed_complete(store):
        return {"entities": len(ENTITIES), "claims_inserted": 0}

    now = datetime.now(UTC)
    body = _fixture_body()
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"synthetic_test_seed": True, "version": 2},
        triggered_by="synthetic_test_seed",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url=TEST_DOCUMENT_URL,
        body_bytes=body,
        http_status=200,
        content_type="text/html",
        discovery_method="synthetic_test_seed",
    )

    with store.session() as session:
        document = _get_or_create_document(session, fetch.id, body, now)
        chunk = _get_or_create_chunk(session, document.id, now)
        extractor_run = ExtractorRun(
            model="synthetic-test-seed",
            prompt_version="synthetic-test-seed-v2",
            started_at=now,
            finished_at=now,
            chunks_processed=1,
            claims_emitted=len(CLAIMS),
            status="complete",
        )
        session.add(extractor_run)
        session.flush()

        entities = {item.key: _get_or_create_entity(session, item, now) for item in ENTITIES}
        inserted_claims = 0
        adjacency: dict[object, set[object]] = {entity.id: set() for entity in entities.values()}

        for item in CLAIMS:
            subject = entities[item.subject]
            obj = entities[item.object]
            adjacency[subject.id].add(obj.id)
            adjacency[obj.id].add(subject.id)
            claim = _get_or_create_claim(session, subject, item, obj, now)
            if claim is None:
                continue
            inserted_claims += 1
            raw = ClaimRaw(
                chunk_id=chunk.id,
                document_id=document.id,
                extractor_run_id=extractor_run.id,
                subject_text=subject.canonical_name,
                subject_type=subject.kind,
                predicate=item.predicate,
                object_text=obj.canonical_name,
                object_type=obj.kind,
                qualifiers={"synthetic_test_seed": True, "is_resolved": item.is_resolved},
                confidence_internal=1.0,
                raw_quote=_claim_sentence(
                    subject.canonical_name, item.predicate, obj.canonical_name
                ),
                extracted_at=now,
            )
            session.add(raw)
            session.flush()
            session.add(ClaimEvidence(claim_id=claim.id, claim_raw_id=raw.id, source_id=source.id))
            _add_mention(session, raw.id, "subject", subject, now)
            _add_mention(session, raw.id, "object", obj, now)

        for item in CLAIMS:
            subject = entities[item.subject]
            obj = entities[item.object]
            _upsert_neighborhood(session, subject.id, obj.id, [item.predicate], now)
            _upsert_neighborhood(session, obj.id, subject.id, [item.predicate], now)
        for item in ENTITIES:
            entity = entities[item.key]
            _upsert_summary(session, entity, item, len(adjacency[entity.id]), now)

        session.commit()

    store.update_source_run(
        run.id, stats={"synthetic_test_seed": True, "version": 2}, finished=True
    )
    store.refresh_source_crawl_counters(source.id)
    store.mark_source_full_recrawl_complete(source.id)
    return {"entities": len(ENTITIES), "claims_inserted": inserted_claims}


def _seed_complete(store: Store) -> bool:
    names = {item.name for item in ENTITIES}
    with store.session() as session:
        entity_count = session.execute(
            select(Entity.canonical_name).where(Entity.canonical_name.in_(names))
        ).all()
        claim_count = 0
        for item in CLAIMS:
            subject = session.execute(
                select(Entity).where(Entity.canonical_name == _entity(item.subject).name)
            ).scalar_one_or_none()
            obj = session.execute(
                select(Entity).where(Entity.canonical_name == _entity(item.object).name)
            ).scalar_one_or_none()
            if subject is None or obj is None:
                continue
            exists = session.execute(
                select(Claim)
                .where(Claim.subject_entity_id == subject.id)
                .where(Claim.predicate == item.predicate)
                .where(Claim.object_entity_id == obj.id)
                .where(Claim.valid_to.is_(None))
            ).scalar_one_or_none()
            claim_count += 1 if exists is not None else 0
    return len(entity_count) == len(ENTITIES) and claim_count == len(CLAIMS)


def _entity(key: str) -> SeedEntity:
    return next(item for item in ENTITIES if item.key == key)


def _fixture_body() -> bytes:
    body = " ".join(
        _claim_sentence(_entity(item.subject).name, item.predicate, _entity(item.object).name)
        for item in CLAIMS
    )
    return f"<html><body>{body}</body></html>".encode("utf-8")


def _claim_sentence(subject: str, predicate: str, obj: str) -> str:
    return f"{subject} {predicate.replace('_', ' ')} {obj}."


def _get_or_create_document(session, fetch_id, body: bytes, now: datetime) -> Document:
    digest = hashlib.sha256(body).digest()
    document = session.execute(
        select(Document).where(Document.content_hash == digest)
    ).scalar_one_or_none()
    if document is None:
        document = Document(
            content_hash=digest,
            cleaned_text=body.decode("utf-8"),
            title="TEST — Coffee Graph synthetic fixture [DEMO]",
            canonical_url=TEST_DOCUMENT_URL,
            language="en",
            word_count=len(body.decode("utf-8").split()),
            first_seen_fetch_id=fetch_id,
            created_at=now,
        )
        session.add(document)
        session.flush()
    if session.get(DocumentFetch, {"document_id": document.id, "fetch_id": fetch_id}) is None:
        session.add(DocumentFetch(document_id=document.id, fetch_id=fetch_id))
    return document


def _get_or_create_chunk(session, document_id, now: datetime) -> Chunk:
    chunk = session.execute(
        select(Chunk).where(Chunk.document_id == document_id).where(Chunk.ordinal == 0)
    ).scalar_one_or_none()
    if chunk is not None:
        return chunk
    text = " ".join(
        _claim_sentence(_entity(item.subject).name, item.predicate, _entity(item.object).name)
        for item in CLAIMS
    )
    chunk = Chunk(
        document_id=document_id, ordinal=0, text=text, token_count=len(text.split()), created_at=now
    )
    session.add(chunk)
    session.flush()
    return chunk


def _get_or_create_entity(session, item: SeedEntity, now: datetime) -> Entity:
    entity = session.execute(
        select(Entity).where(Entity.canonical_name == item.name)
    ).scalar_one_or_none()
    if entity is None:
        entity = Entity(kind=item.kind, canonical_name=item.name, created_at=now, updated_at=now)
        session.add(entity)
        session.flush()
    alias = item.name.replace(" [DEMO]", "")
    existing_alias = session.execute(
        select(EntityAlias)
        .where(EntityAlias.entity_id == entity.id)
        .where(EntityAlias.alias == alias)
    ).scalar_one_or_none()
    if existing_alias is None:
        session.add(
            EntityAlias(
                entity_id=entity.id,
                alias=alias,
                confidence=1.0,
                source="synthetic_test_seed",
                created_at=now,
            )
        )
    return entity


def _upsert_summary(
    session,
    entity: Entity,
    item: SeedEntity,
    connection_count: int,
    now: datetime,
) -> None:
    summary = session.get(EntitySummary, entity.id)
    if summary is None:
        session.add(
            EntitySummary(
                entity_id=entity.id,
                display_name=item.name,
                primary_attributes=item.attributes,
                connection_count=connection_count,
                source_count=1,
                last_updated=now,
            )
        )
        return
    summary.primary_attributes = item.attributes
    summary.connection_count = connection_count
    summary.source_count = 1
    summary.last_updated = now


def _get_or_create_claim(
    session,
    subject: Entity,
    item: SeedClaim,
    obj: Entity,
    now: datetime,
) -> Claim | None:
    existing = session.execute(
        select(Claim)
        .where(Claim.subject_entity_id == subject.id)
        .where(Claim.predicate == item.predicate)
        .where(Claim.object_entity_id == obj.id)
        .where(Claim.valid_to.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        existing.qualifiers = {"synthetic_test_seed": True, "is_resolved": item.is_resolved}
        return None
    claim = Claim(
        subject_entity_id=subject.id,
        predicate=item.predicate,
        object_entity_id=obj.id,
        object_value=None,
        qualifiers={"synthetic_test_seed": True, "is_resolved": item.is_resolved},
        status="active",
        first_seen_at=now,
        last_corroborated_at=now,
    )
    session.add(claim)
    session.flush()
    return claim


def _add_mention(session, claim_raw_id, position: str, entity: Entity, now: datetime) -> None:
    session.add(
        EntityMention(
            claim_raw_id=claim_raw_id,
            position=position,
            entity_id=entity.id,
            mention_text=entity.canonical_name,
            resolution_method="new_entity",
            resolution_confidence=1.0,
            resolved_at=now,
        )
    )


def _upsert_neighborhood(
    session, entity_id, neighbor_id, predicates: list[str], now: datetime
) -> None:
    row = session.get(EntityNeighborhood, {"entity_id": entity_id, "neighbor_id": neighbor_id})
    if row is None:
        session.add(
            EntityNeighborhood(
                entity_id=entity_id,
                neighbor_id=neighbor_id,
                predicates=predicates,
                evidence_count=1,
                last_updated=now,
            )
        )
    else:
        row.predicates = predicates
        row.evidence_count = 1
        row.last_updated = now
