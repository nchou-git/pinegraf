from __future__ import annotations

import pytest
from sqlalchemy import func, select

from backend.db.models import Claim, ClaimEvidence, ClaimRaw, Entity, EntityMention, ExtractorRun
from backend.extraction.extractor import extract_claims
from backend.normalization import normalizer
from backend.normalization.chunker import Chunk
from backend.parse.orchestrator import run_full_parse


@pytest.mark.asyncio
async def test_extraction_heuristic_returns_claim(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await extract_claims("Sam Brooks partnered with Mia Chen to license the invention.")

    assert result.model
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim.subject_text == "Sam Brooks"
    assert claim.predicate == "partnered_with"
    assert claim.object_text == "Mia Chen"
    assert claim.object_type == "person"


@pytest.mark.asyncio
async def test_full_parse_normalizes_extracts_raw_claims_and_completes(store, monkeypatch) -> None:
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

    text = "Sam Brooks partnered with Mia Chen to license the invention."
    monkeypatch.setattr(normalizer, "clean_html", lambda raw: (text, "Story"))
    monkeypatch.setattr(normalizer, "detect_language", lambda value: "en")
    monkeypatch.setattr(normalizer, "chunk_text", lambda value: [Chunk(text=value, token_count=12)])

    async def fake_embed(chunks: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in chunks]

    monkeypatch.setattr(normalizer, "embed_chunks", fake_embed)

    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
    )

    touched = await run_full_parse(source.id, store=store, progress_run_id=parse_run.id)

    with store.session() as session:
        raw_claim = session.execute(
            select(ClaimRaw).where(ClaimRaw.predicate == "partnered_with")
        ).scalar_one()
        extractor_run = session.execute(select(ExtractorRun)).scalar_one()
        entity_count = session.execute(select(func.count()).select_from(Entity)).scalar_one()
        promoted_claim_count = session.execute(select(func.count()).select_from(Claim)).scalar_one()
        evidence_count = session.execute(
            select(func.count()).select_from(ClaimEvidence)
        ).scalar_one()
        mention_count = session.execute(
            select(func.count()).select_from(EntityMention)
        ).scalar_one()
        finished_run = store.get_source_run(parse_run.id)

    assert touched == set()
    assert raw_claim.subject_text == "Sam Brooks"
    assert raw_claim.object_text == "Mia Chen"
    assert raw_claim.object_type == "person"
    assert extractor_run.status == "complete"
    assert extractor_run.chunks_processed == 1
    assert extractor_run.claims_emitted == 1
    assert entity_count == 0
    assert promoted_claim_count == 0
    assert evidence_count == 0
    assert mention_count == 0
    assert finished_run.status == "complete"
    assert finished_run.stats["status"] == "complete"
    assert finished_run.stats["stage"] == "complete"
    assert finished_run.stats["percent"] == 100.0
    assert "resolved_entities" not in finished_run.stats
    assert "touched_claims" not in finished_run.stats
    assert "projected_entities" not in finished_run.stats
