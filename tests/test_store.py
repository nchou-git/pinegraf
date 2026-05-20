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


def test_position_upsert_avoids_duplicates_for_same_match_key(tmp_path) -> None:
    store = make_store(tmp_path)
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Profile text",
    )
    first = {
        "category": "position",
        "content": (
            '{"company":"Acme Corp","title":"COO","location":null,'
            '"start_date":"2024-01","end_date":null,"position_type":"full_time","is_current":true}'
        ),
        "confidence": "medium",
        "validation_verdict": "keep",
    }
    second = {
        "category": "position",
        "content": (
            '{"company":"  acme   corp ","title":"  coo ","location":null,'
            '"start_date":"2024-01","end_date":"2026-01","position_type":"full_time","is_current":false}'
        ),
        "confidence": "high",
        "validation_verdict": "uncertain",
    }

    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[first],
        connections=[],
        projects=[],
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[second],
        connections=[],
        projects=[],
    )

    rows = [fact for fact in store.list_facts() if fact.category == "position"]
    assert len(rows) == 1
    assert rows[0].confidence == "high"
    assert rows[0].validation_verdict == "uncertain"
    positions = store.get_positions_for_alum("Jane Doe", frozenset({"keep", "uncertain"}))
    assert positions[0]["is_current"] is False
    assert positions[0]["end_date"] == "2026-01"


def test_positions_sort_current_first(tmp_path) -> None:
    store = make_store(tmp_path)
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Profile text",
    )

    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[
            {
                "category": "position",
                "content": (
                    '{"company":"Past Co","title":"VP","location":null,"start_date":"2020",'
                    '"end_date":"2024-05","position_type":"full_time","is_current":false}'
                ),
            },
            {
                "category": "position",
                "content": (
                    '{"company":"Current Co","title":"Advisor","location":null,"start_date":"2023",'
                    '"end_date":null,"position_type":"advisor","is_current":true}'
                ),
            },
        ],
        connections=[],
        projects=[],
    )

    positions = store.get_positions_for_alum("Jane Doe")
    assert positions[0]["company"] == "Current Co"
    assert positions[0]["is_current"] is True


def test_profile_current_company_backfills_from_first_current_position(tmp_path) -> None:
    store = make_store(tmp_path)
    store.upsert_profile(name="Jane Doe", class_year="T'24")
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Profile text",
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[
            {
                "category": "position",
                "content": (
                    '{"company":"Current Co","title":"CEO","location":null,"start_date":"2023",'
                    '"end_date":null,"position_type":"full_time","is_current":true}'
                ),
            }
        ],
        connections=[],
        projects=[],
    )

    top = store.get_positions_for_alum("Jane Doe")[0]
    store.upsert_profile(
        name="Jane Doe",
        current_company=top["company"],
        current_title=top["title"],
    )

    profile = store.list_profiles()[0]
    assert profile.current_company == "Current Co"
    assert profile.current_title == "CEO"


def test_position_date_sort_year_vs_year_month(tmp_path) -> None:
    store = make_store(tmp_path)
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Profile text",
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[
            {
                "category": "position",
                "content": (
                    '{"company":"Year Only","title":"Role A","location":null,"start_date":"2023",'
                    '"end_date":null,"position_type":"advisor","is_current":true}'
                ),
            },
            {
                "category": "position",
                "content": (
                    '{"company":"Year Month","title":"Role B","location":null,'
                    '"start_date":"2024-06",'
                    '"end_date":null,"position_type":"board","is_current":true}'
                ),
            },
        ],
        connections=[],
        projects=[],
    )

    positions = store.get_positions_for_alum("Jane Doe")
    assert positions[0]["company"] == "Year Month"
