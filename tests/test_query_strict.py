from __future__ import annotations

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
