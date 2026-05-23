from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.db.store import Store
from backend.pipeline.query import (
    DEEP_SYSTEM_PROMPT,
    DeepQueryClient,
    MockDeepQueryClient,
    QueryExpansion,
)
from backend.resolution.embeddings import DeterministicEmbeddingClient


def test_deep_query_uses_raw_pages_and_citation_prompt_on_sqlite_fallback(
    tmp_path,
    mocker: MockerFixture,
) -> None:
    store = Store(f"sqlite:///{tmp_path / 'deep.db'}")
    store.init_db()
    first = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/old",
        page_title="Old",
        page_text="Old page about Dartmouth Tuck and Acme.",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    second = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/new",
        page_title="New",
        page_text="New page about Gyrobike.",
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    fake_parse = mocker.Mock(
        return_value=SimpleNamespace(
            output_parsed=QueryExpansion(queries=["gyrobike"]),
            usage=SimpleNamespace(input_tokens=5, output_tokens=2),
        )
    )
    fake_create = mocker.Mock(
        return_value=SimpleNamespace(
            output_text="Deep answer.",
            usage=SimpleNamespace(input_tokens=10, output_tokens=4),
        )
    )
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create, parse=fake_parse))
    fake_openai = mocker.patch("backend.pipeline.query.OpenAI", return_value=fake_client)

    pages = store.raw_pages_fts_search("gyrobike", limit=20)
    answer = DeepQueryClient(
        store=store,
        api_key="test-key",
        embedding_client=DeterministicEmbeddingClient(),
    ).answer_question("What is Gyrobike?")

    assert [page.id for page in pages] == [first.id, second.id]
    fake_openai.assert_called_once_with(api_key="test-key", max_retries=0)
    assert fake_parse.call_args.kwargs["model"] == "gpt-5.4-mini"
    kwargs = fake_create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert kwargs["input"][0]["content"] == DEEP_SYSTEM_PROMPT
    user_prompt = kwargs["input"][1]["content"]
    assert "https://example.com/old" in user_prompt
    assert "https://example.com/new" in user_prompt
    assert "source_url:" in user_prompt
    assert answer.answer == "Deep answer."


def test_mock_deep_query_expands_common_misspelling(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'mock_deep.db'}")
    store.init_db()
    store.save_raw_page(
        alum_name="Errik Anderson",
        source_url="https://example.com/gyrobike",
        page_title="Gyrobike",
        page_text="Errik Anderson and Daniella Reichstetter worked on Gyrobike.",
    )

    misspelled = MockDeepQueryClient(store).answer_question("who worked on gyrobyke")
    spelled = MockDeepQueryClient(store).answer_question("who worked on gyrobike")

    assert "https://example.com/gyrobike" in misspelled.answer
    assert misspelled.answer == spelled.answer
