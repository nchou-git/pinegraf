from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from backend.db.store import Store


def make_store(tmp_path) -> Store:
    store = Store(f"sqlite:///{tmp_path / 'test.db'}")
    store.init_db()
    return store


def test_raw_pages_save_list_and_dedupe(tmp_path) -> None:
    store = make_store(tmp_path)

    first = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe is CEO of ExampleCo.",
    )
    second = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Duplicate",
        page_text="Duplicate",
    )

    pages = store.list_raw_pages()
    assert len(pages) == 1
    assert first.id == second.id == pages[0].id
    assert pages[0].parsed_at is None


def test_parsed_at_flag(tmp_path) -> None:
    store = make_store(tmp_path)
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe is CEO of ExampleCo.",
    )

    assert [p.id for p in store.list_pages_to_parse()] == [page.id]
    store.mark_raw_page_parsed(page.id, datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert store.list_pages_to_parse() == []
    assert store.list_pages_to_parse(force=True)[0].parsed_at is not None


def test_jsonb_profile_fields_round_trip_on_sqlite(tmp_path) -> None:
    store = make_store(tmp_path)

    store.upsert_profile(
        name="Jane Doe",
        class_year="T'24",
        past_companies=["Beta Inc"],
        education=["Dartmouth Tuck MBA"],
    )

    profile = store.list_profiles()[0]
    assert profile.past_companies == ["Beta Inc"]
    assert profile.education == ["Dartmouth Tuck MBA"]


def test_structured_rows_have_source_fk_and_sqlite_enforces_integrity(tmp_path) -> None:
    store = make_store(tmp_path)
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe is CEO of ExampleCo.",
    )

    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[{"category": "career", "content": "Jane is CEO.", "confidence": "high"}],
        connections=[],
        projects=[],
    )

    fact = store.list_facts()[0]
    assert fact.source_raw_page_id == page.id
    assert fact.raw_page.source_url == "https://example.com/jane"

    with pytest.raises(IntegrityError):
        store.replace_structured_items(
            raw_page_id=999,
            alum_name="Jane Doe",
            facts=[{"category": "career", "content": "Invalid source."}],
            connections=[],
            projects=[],
        )
