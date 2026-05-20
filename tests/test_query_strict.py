from __future__ import annotations

import json
from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.db.store import Store
from backend.pipeline.query import OpenAIQueryClient


def test_strict_query_reads_keep_rows_only(tmp_path, mocker: MockerFixture) -> None:
    store = Store(f"sqlite:///{tmp_path / 'strict.db'}")
    store.init_db()
    store.upsert_profile(name="Jane Doe", class_year="T'24", current_company="Acme Corp")
    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe is COO at Acme Corp.",
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Jane Doe",
        facts=[
            {
                "category": "career",
                "content": "Keep fact.",
                "validation_verdict": "keep",
            },
            {
                "category": "career",
                "content": "Drop fact.",
                "validation_verdict": "drop",
            },
        ],
        connections=[],
        projects=[],
    )
    fake_create = mocker.Mock(return_value=SimpleNamespace(output_text="Strict answer."))
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    fake_openai = mocker.patch("backend.pipeline.query.OpenAI", return_value=fake_client)

    answer = OpenAIQueryClient(store=store, api_key="test-key").answer_question(
        "Who works at Acme?"
    )

    fake_openai.assert_called_once_with(api_key="test-key")
    assert fake_create.call_args.kwargs["model"] == "gpt-5.4-mini"
    prompt = fake_create.call_args.kwargs["input"]
    assert "Keep fact." in prompt
    assert "Drop fact." not in prompt
    assert answer.answer == "Strict answer."


def test_strict_query_prompt_includes_structured_concurrent_positions(
    tmp_path, mocker: MockerFixture
) -> None:
    store = Store(f"sqlite:///{tmp_path / 'strict_positions.db'}")
    store.init_db()
    store.upsert_profile(
        name="Daniella Reichstetter",
        class_year="T'20",
        current_company="Legacy Co",
        current_title="Legacy Title",
    )
    page = store.save_raw_page(
        alum_name="Daniella Reichstetter",
        source_url="https://example.com/daniella",
        page_title="Daniella Profile",
        page_text="Daniella has multiple roles.",
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Daniella Reichstetter",
        facts=[
            {
                "category": "position",
                "content": json.dumps(
                    {
                        "company": "Tuck School of Business",
                        "title": "Board Member",
                        "start_date": "2023-01",
                        "end_date": None,
                    }
                ),
                "validation_verdict": "keep",
            },
            {
                "category": "position",
                "content": json.dumps(
                    {
                        "company": "Gyrobike",
                        "title": "Operating Partner",
                        "start_date": "2024-01",
                        "end_date": None,
                    }
                ),
                "validation_verdict": "keep",
            },
        ],
        connections=[],
        projects=[],
    )
    fake_create = mocker.Mock(return_value=SimpleNamespace(output_text="Strict answer."))
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    mocker.patch("backend.pipeline.query.OpenAI", return_value=fake_client)

    OpenAIQueryClient(store=store, api_key="test-key").answer_question(
        "What are Daniella Reichstetter current positions?"
    )

    prompt = fake_create.call_args.kwargs["input"]
    assert "Tuck School of Business" in prompt
    assert "Gyrobike" in prompt
    assert ("concurrent" in prompt.lower()) or ("multiple" in prompt.lower())
