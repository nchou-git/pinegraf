from __future__ import annotations

from sqlalchemy import select

from backend.db.models import Entity, EntityAlias
from backend.db.store import Store
from backend.resolution.backfill import backfill_entity_embeddings
from backend.resolution.embeddings import DeterministicEmbeddingClient
from backend.resolution.entity_resolver import resolve_or_create


def make_store(tmp_path) -> Store:
    store = Store(f"sqlite:///{tmp_path / 'resolver.db'}")
    store.init_db()
    return store


def test_same_name_same_class_year_resolves_to_same_entity(tmp_path) -> None:
    store = make_store(tmp_path)

    with store.session() as session:
        first = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        second = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        session.commit()

    assert first == second


def test_same_name_different_class_year_creates_distinct_entities(tmp_path) -> None:
    store = make_store(tmp_path)

    with store.session() as session:
        first = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        second = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'25"})
        session.commit()

    assert first != second


def test_same_name_without_context_is_never_auto_merged(tmp_path) -> None:
    store = make_store(tmp_path)

    with store.session() as session:
        first = resolve_or_create("Jane Doe", session=session)
        second = resolve_or_create("Jane Doe", session=session)
        session.commit()

    assert first != second


def test_alias_matching_is_case_insensitive(tmp_path) -> None:
    store = make_store(tmp_path)

    with store.session() as session:
        first = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        second = resolve_or_create("jane doe", session=session, context={"class_year": "T'24"})
        session.commit()

    assert first == second


def test_alias_matching_normalizes_whitespace(tmp_path) -> None:
    store = make_store(tmp_path)

    with store.session() as session:
        first = resolve_or_create(" Jane   Doe ", session=session, context={"class_year": "T'24"})
        second = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        session.commit()

    assert first == second


def test_embedding_resolver_merges_errik_variants_with_tuck_context(tmp_path) -> None:
    store = make_store(tmp_path)
    context = {"class_year": "T'07", "school": "Tuck"}

    with store.session() as session:
        first = resolve_or_create("Errik B. Anderson", session=session, context=context)
        second = resolve_or_create("Errik Anderson", session=session, context=context)
        third = resolve_or_create("E. Anderson D'00 Th'06 T'07", session=session, context=context)
        session.commit()

    assert first == second == third
    with store.session() as session:
        entity = session.get(Entity, first)
        aliases = list(
            session.execute(
                select(EntityAlias.alias)
                .where(EntityAlias.entity_id == first)
                .order_by(EntityAlias.alias.asc())
            ).scalars()
        )
    assert entity is not None
    assert entity.name_embedding is not None
    assert entity.context_embedding is not None
    assert "errik b. anderson" in aliases
    assert "e. anderson d'00 th'06 t'07" in aliases


def test_backfill_entity_embeddings_populates_existing_entities(tmp_path) -> None:
    store = make_store(tmp_path)
    with store.session() as session:
        entity_id = resolve_or_create("Jane Doe", session=session, context={"class_year": "T'24"})
        entity = session.get(Entity, entity_id)
        assert entity is not None
        entity.name_embedding = None
        entity.context_embedding = None
        session.commit()

    summary = backfill_entity_embeddings(
        store,
        embedding_client=DeterministicEmbeddingClient(),
    )

    assert summary.entities_seen == 1
    assert summary.entities_updated == 1
    with store.session() as session:
        entity = session.get(Entity, entity_id)
    assert entity is not None
    assert entity.name_embedding is not None
    assert entity.context_embedding is not None
