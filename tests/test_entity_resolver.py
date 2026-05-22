from __future__ import annotations

from backend.db.store import Store
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
