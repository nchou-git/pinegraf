from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models import (
    Claim,
    Entity,
    EntityAlias,
    EntityDisambiguationCandidate,
    EntitySummary,
)
from backend.resolution import resolver
from backend.resolution.llm_disambiguator import DisambiguationResult


async def _embed_for_text(text: str) -> list[float]:
    del text
    return _vector(1.0, 0.0)


def _add_entity(store, name: str, vector: list[float]) -> Entity:
    with store.session() as session:
        entity = Entity(kind="person", canonical_name=name, embedding=vector)
        session.add(entity)
        session.commit()
        return entity


def _vector(x: float, y: float) -> list[float]:
    return [x, y, *([0.0] * 1534)]


@pytest.mark.asyncio
async def test_ambiguous_llm_match_links_existing_and_adds_alias(store, monkeypatch) -> None:
    entity = _add_entity(store, "Erik Snowberg", _vector(0.7, 0.714142842))
    calls = []

    async def fake_disambiguate(mention, candidates, context_chunk):
        calls.append((mention, candidates, context_chunk))
        return DisambiguationResult(entity.id, 0.92, "Typo of Erik Snowberg")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fake_disambiguate)

    result = await resolver.resolve_mention(
        "Erik Snoberg",
        "person",
        store=store,
        context_chunk="Erik Snoberg teaches economics.",
    )

    assert result.entity_id == entity.id
    assert result.method == "llm"
    assert len(calls) == 1
    with store.session() as session:
        alias = session.execute(
            select(EntityAlias).where(EntityAlias.entity_id == entity.id)
        ).scalar_one()
    assert alias.alias == "Erik Snoberg"


@pytest.mark.asyncio
async def test_ambiguous_llm_new_entity_creates_entity(store, monkeypatch) -> None:
    _add_entity(store, "Erik Snowberg", _vector(0.7, 0.714142842))

    async def fake_disambiguate(mention, candidates, context_chunk):
        del mention, candidates, context_chunk
        return DisambiguationResult(None, 0.88, "Different person")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fake_disambiguate)

    result = await resolver.resolve_mention("Alex Example", "person", store=store)

    assert result.method == "new_entity"
    with store.session() as session:
        created = session.get(Entity, result.entity_id)
    assert created.canonical_name == "Alex Example"


@pytest.mark.asyncio
async def test_high_name_similarity_uses_llm(store, monkeypatch) -> None:
    entity = _add_entity(store, "Erik Snowberg", _vector(0.99, 0.14106736))

    async def fake_disambiguate(mention, candidates, context_chunk):
        del mention, candidates, context_chunk
        return DisambiguationResult(entity.id, 0.92, "Typo of Erik Snowberg")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fake_disambiguate)

    result = await resolver.resolve_mention("Erik Snoberg", "person", store=store)

    assert result.entity_id == entity.id
    assert result.method == "llm"


@pytest.mark.asyncio
async def test_low_cosine_skips_llm_and_creates_new(store, monkeypatch) -> None:
    _add_entity(store, "Erik Snowberg", _vector(0.2, 0.979795897))

    async def fail_disambiguate(*args, **kwargs):
        raise AssertionError("LLM should not be called")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fail_disambiguate)

    result = await resolver.resolve_mention(
        f"New Person {uuid.uuid4().hex[:4]}",
        "person",
        store=store,
    )

    assert result.method == "new_entity"


@pytest.mark.asyncio
async def test_class_year_conflict_excludes_candidate(store, monkeypatch) -> None:
    entity = _add_entity(store, "Erik Snowberg", _vector(0.7, 0.714142842))
    with store.session() as session:
        session.add(
            EntitySummary(
                entity_id=entity.id,
                display_name="Erik Snowberg",
                primary_attributes={"class_year": 2000},
            )
        )
        session.commit()

    async def fail_disambiguate(*args, **kwargs):
        raise AssertionError("Contradictory class year should block LLM merge")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fail_disambiguate)

    result = await resolver.resolve_mention(
        "Erik Snoberg T'15",
        "person",
        store=store,
        context_chunk="Erik Snoberg T'15 worked on a student venture.",
    )

    assert result.method == "new_entity"
    assert result.entity_id != entity.id
    with store.session() as session:
        candidate = session.execute(select(EntityDisambiguationCandidate)).scalar_one_or_none()
    assert candidate is None


@pytest.mark.asyncio
async def test_affiliation_conflict_records_near_miss_without_llm(store, monkeypatch) -> None:
    entity = _add_entity(store, "Alex Example", _vector(0.7, 0.714142842))
    with store.session() as session:
        org = Entity(kind="org", canonical_name="Acme Labs")
        session.add(org)
        session.flush()
        session.add(
            Claim(
                subject_entity_id=entity.id,
                predicate="employed_by",
                object_entity_id=org.id,
            )
        )
        session.commit()

    async def fake_disambiguate(mention, candidates, context_chunk):
        del mention, candidates, context_chunk
        return DisambiguationResult(entity.id, 0.92, "Names are similar")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fake_disambiguate)

    result = await resolver.resolve_mention(
        "Alex Exampel",
        "person",
        store=store,
        context_chunk="Alex Exampel works at Widget Labs on a synthetic project.",
    )

    assert result.method == "new_entity"
    assert result.entity_id != entity.id
    with store.session() as session:
        candidate = session.execute(select(EntityDisambiguationCandidate)).scalar_one()
    assert candidate.candidate_entity_id == entity.id
    assert candidate.llm_decision == "near_miss_review"


@pytest.mark.asyncio
async def test_location_conflict_records_near_miss_without_llm(store, monkeypatch) -> None:
    entity = _add_entity(store, "Jordan Sample", _vector(0.7, 0.714142842))
    with store.session() as session:
        place = Entity(kind="place", canonical_name="Denver")
        session.add(place)
        session.flush()
        session.add(
            Claim(
                subject_entity_id=entity.id,
                predicate="located_in",
                object_entity_id=place.id,
            )
        )
        session.commit()

    async def fake_disambiguate(mention, candidates, context_chunk):
        del mention, candidates, context_chunk
        return DisambiguationResult(entity.id, 0.92, "Names are similar")

    monkeypatch.setattr(resolver, "embed_text", _embed_for_text)
    monkeypatch.setattr(resolver, "disambiguate", fake_disambiguate)

    result = await resolver.resolve_mention(
        "Jordan Sampl",
        "person",
        store=store,
        context_chunk="Jordan Sampl is based in Boston while advising a synthetic venture.",
    )

    assert result.method == "new_entity"
    assert result.entity_id != entity.id
    with store.session() as session:
        candidate = session.execute(select(EntityDisambiguationCandidate)).scalar_one()
    assert candidate.candidate_entity_id == entity.id
    assert candidate.llm_decision == "near_miss_review"
