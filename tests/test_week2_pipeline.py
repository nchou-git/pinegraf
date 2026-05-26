from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.db.models import (
    Claim,
    ClaimEvidence,
    Entity,
    EntityMention,
    EntityNeighborhood,
    EntitySummary,
)
from backend.extraction.cascading_extractor import extract_claims
from backend.normalization import normalizer
from backend.normalization.chunker import Chunk
from backend.pipeline.orchestrator import run_full_pipeline
from backend.resolution.resolver import resolve_mention


@pytest.mark.asyncio
async def test_extraction_heuristic_returns_claim(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await extract_claims(
        "Errik Anderson partnered with Daniella Reichstetter to license the invention."
    )

    assert result.model
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.subject_text == "Errik Anderson"
    assert claim.predicate == "partnered_with"
    assert claim.object_text == "Daniella Reichstetter"
    assert claim.object_type == "person"


@pytest.mark.asyncio
async def test_resolution_exact_match_normalizes_tuck_suffix(store) -> None:
    with store.session() as session:
        entity = Entity(kind="person", canonical_name="Errik Anderson")
        session.add(entity)
        session.commit()
        entity_id = entity.id

    resolution = await resolve_mention("Errik Anderson T'07", "person", store=store)

    assert resolution is not None
    assert resolution.entity_id == entity_id
    assert resolution.method == "exact_match"


@pytest.mark.asyncio
async def test_full_pipeline_promotes_claims_and_builds_projections(store, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = store.upsert_source(
        kind="domain",
        identifier="example.com",
        trust_weight=0.8,
        display_name="Example",
    )
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"urls": ["https://example.com/story"]},
        triggered_by="test",
    )
    store.add_fetch(
        source_run_id=run.id,
        url="https://example.com/story",
        body_bytes=b"<html><main>story</main></html>",
        http_status=200,
        content_type="text/html",
    )

    text = "Errik Anderson partnered with Daniella Reichstetter to license the invention."
    monkeypatch.setattr(normalizer, "clean_html", lambda raw: (text, "Story"))
    monkeypatch.setattr(normalizer, "detect_language", lambda value: "en")
    monkeypatch.setattr(normalizer, "chunk_text", lambda value: [Chunk(text=value, token_count=12)])

    async def fake_embed(chunks: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in chunks]

    monkeypatch.setattr(normalizer, "embed_chunks", fake_embed)

    rebuilt = await run_full_pipeline(run.id, store=store)

    with store.session() as session:
        errik = session.execute(
            select(Entity).where(Entity.canonical_name == "Errik Anderson")
        ).scalar_one()
        daniella = session.execute(
            select(Entity).where(Entity.canonical_name == "Daniella Reichstetter")
        ).scalar_one()
        claim = session.execute(
            select(Claim).where(Claim.predicate == "partnered_with")
        ).scalar_one()
        evidence_count = session.execute(
            select(func.count()).select_from(ClaimEvidence)
        ).scalar_one()
        mention_count = session.execute(
            select(func.count()).select_from(EntityMention)
        ).scalar_one()
        summary = session.get(EntitySummary, errik.id)
        neighborhood = session.get(
            EntityNeighborhood,
            {"entity_id": errik.id, "neighbor_id": daniella.id},
        )

    assert errik.id in rebuilt
    assert claim.subject_entity_id == errik.id
    assert claim.object_entity_id == daniella.id
    assert claim.confidence_score > 0
    assert evidence_count == 1
    assert mention_count == 2
    assert summary is not None
    assert summary.connection_count == 1
    assert neighborhood is not None
    assert neighborhood.predicates == ["partnered_with"]
