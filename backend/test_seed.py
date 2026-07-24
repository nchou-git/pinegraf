from __future__ import annotations

import hashlib
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


def seed_synthetic_test_data(store: Store) -> dict[str, int]:
    source = store.upsert_source(
        kind="domain",
        identifier=TEST_SOURCE_IDENTIFIER,
        trust_weight=0.8,
        respect_robots=True,
        display_name=TEST_SOURCE_DISPLAY_NAME,
        notes="Obvious fake test data for the public Coffee Graph demo.",
    )
    if _already_seeded(store):
        return {"claims_inserted": 0}
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"synthetic_test_seed": True},
        triggered_by="synthetic_test_seed",
        status="complete",
    )
    body = (
        "<html><body>"
        "TEST — Ava Example works at TEST — Bytebrew Labs [DEMO]. "
        "TEST — Ava Example contributes to TEST — Pinecone Scholarship Project [DEMO]. "
        "TEST — Bytebrew Labs [DEMO] sponsors TEST — Pinecone Scholarship Project [DEMO]."
        "</body></html>"
    ).encode("utf-8")
    fetch = store.add_fetch(
        source_run_id=run.id,
        url=TEST_DOCUMENT_URL,
        body_bytes=body,
        http_status=200,
        content_type="text/html",
        discovery_method="synthetic_test_seed",
    )

    now = datetime.now(UTC)
    with store.session() as session:
        document = _get_or_create_document(session, fetch.id, body, now)
        chunk = _get_or_create_chunk(session, document.id, now)
        extractor_run = ExtractorRun(
            model="synthetic-test-seed",
            prompt_version="synthetic-test-seed-v1",
            started_at=now,
            finished_at=now,
            chunks_processed=1,
            claims_emitted=3,
            status="complete",
        )
        session.add(extractor_run)
        session.flush()

        ava = _get_or_create_entity(
            session,
            "TEST — Ava Example [DEMO]",
            "person",
            {
                "current_title": "TEST — Demo fellow",
                "current_employer": "TEST — Bytebrew Labs [DEMO]",
                "class_year": "TEST — 2099",
            },
            now,
        )
        org = _get_or_create_entity(
            session,
            "TEST — Bytebrew Labs [DEMO]",
            "org",
            {"sector": "TEST — Synthetic coffee analytics"},
            now,
        )
        project = _get_or_create_entity(
            session,
            "TEST — Pinecone Scholarship Project [DEMO]",
            "project",
            {"theme": "TEST — Synthetic scholarships"},
            now,
        )

        inserted_claims = 0
        for subject, predicate, obj in (
            (ava, "works_at", org),
            (ava, "contributes_to", project),
            (org, "sponsors", project),
        ):
            claim = _get_or_create_claim(session, subject, predicate, obj, now)
            if claim is None:
                continue
            inserted_claims += 1
            raw = ClaimRaw(
                chunk_id=chunk.id,
                document_id=document.id,
                extractor_run_id=extractor_run.id,
                subject_text=subject.canonical_name,
                subject_type=subject.kind,
                predicate=predicate,
                object_text=obj.canonical_name,
                object_type=obj.kind,
                qualifiers={"synthetic_test_seed": True},
                confidence_internal=1.0,
                raw_quote=f"{subject.canonical_name} {predicate} {obj.canonical_name}.",
                extracted_at=now,
            )
            session.add(raw)
            session.flush()
            session.add(ClaimEvidence(claim_id=claim.id, claim_raw_id=raw.id, source_id=source.id))
            session.add(
                EntityMention(
                    claim_raw_id=raw.id,
                    position="subject",
                    entity_id=subject.id,
                    mention_text=subject.canonical_name,
                    resolution_method="new_entity",
                    resolution_confidence=1.0,
                    resolved_at=now,
                )
            )
            session.add(
                EntityMention(
                    claim_raw_id=raw.id,
                    position="object",
                    entity_id=obj.id,
                    mention_text=obj.canonical_name,
                    resolution_method="new_entity",
                    resolution_confidence=1.0,
                    resolved_at=now,
                )
            )

        _upsert_neighborhood(session, ava.id, org.id, ["works_at"], now)
        _upsert_neighborhood(session, org.id, ava.id, ["works_at"], now)
        _upsert_neighborhood(session, ava.id, project.id, ["contributes_to"], now)
        _upsert_neighborhood(session, project.id, ava.id, ["contributes_to"], now)
        _upsert_neighborhood(session, org.id, project.id, ["sponsors"], now)
        _upsert_neighborhood(session, project.id, org.id, ["sponsors"], now)

        session.commit()

    store.update_source_run(run.id, stats={"synthetic_test_seed": True}, finished=True)
    store.refresh_source_crawl_counters(source.id)
    store.mark_source_full_recrawl_complete(source.id)
    return {"claims_inserted": inserted_claims}


def _already_seeded(store: Store) -> bool:
    with store.session() as session:
        return (
            session.execute(
                select(Entity).where(Entity.canonical_name == "TEST — Ava Example [DEMO]")
            ).scalar_one_or_none()
            is not None
        )


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
            word_count=31,
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
    chunk = Chunk(
        document_id=document_id,
        ordinal=0,
        text=(
            "TEST — Ava Example works at TEST — Bytebrew Labs [DEMO]. "
            "TEST — Ava Example contributes to TEST — Pinecone Scholarship Project [DEMO]."
        ),
        token_count=22,
        created_at=now,
    )
    session.add(chunk)
    session.flush()
    return chunk


def _get_or_create_entity(
    session,
    name: str,
    kind: str,
    attributes: dict[str, object],
    now: datetime,
) -> Entity:
    entity = session.execute(
        select(Entity).where(Entity.canonical_name == name)
    ).scalar_one_or_none()
    if entity is None:
        entity = Entity(kind=kind, canonical_name=name, created_at=now, updated_at=now)
        session.add(entity)
        session.flush()
    summary = session.get(EntitySummary, entity.id)
    if summary is None:
        session.add(
            EntitySummary(
                entity_id=entity.id,
                display_name=name,
                primary_attributes=attributes,
                connection_count=2,
                source_count=1,
                last_updated=now,
            )
        )
    else:
        summary.primary_attributes = attributes
        summary.connection_count = 2
        summary.source_count = 1
        summary.last_updated = now
    alias = name.replace(" [DEMO]", "")
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


def _get_or_create_claim(
    session,
    subject: Entity,
    predicate: str,
    obj: Entity,
    now: datetime,
) -> Claim | None:
    existing = session.execute(
        select(Claim)
        .where(Claim.subject_entity_id == subject.id)
        .where(Claim.predicate == predicate)
        .where(Claim.object_entity_id == obj.id)
        .where(Claim.valid_to.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        return None
    claim = Claim(
        subject_entity_id=subject.id,
        predicate=predicate,
        object_entity_id=obj.id,
        object_value=None,
        qualifiers={"synthetic_test_seed": True},
        status="active",
        first_seen_at=now,
        last_corroborated_at=now,
    )
    session.add(claim)
    session.flush()
    return claim


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
