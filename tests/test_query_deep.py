from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from pytest_mock import MockerFixture

from backend.db.store import Store
from backend.pipeline.query import DEEP_SYSTEM_PROMPT, DeepQueryClient


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
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/new",
        page_title="New",
        page_text="New page about Gyrobike.",
        fetched_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    fake_create = mocker.Mock(return_value=SimpleNamespace(output_text="Deep answer."))
    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    fake_openai = mocker.patch("backend.pipeline.query.OpenAI", return_value=fake_client)

    pages = store.raw_pages_fts_search("gyrobike", limit=20)
    answer = DeepQueryClient(store=store, api_key="test-key").answer_question("What is Gyrobike?")

    assert [page.id for page in pages] == [first.id, second.id]
    fake_openai.assert_called_once_with(api_key="test-key")
    kwargs = fake_create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert kwargs["input"][0]["content"] == DEEP_SYSTEM_PROMPT
    user_prompt = kwargs["input"][1]["content"]
    assert "https://example.com/old" in user_prompt
    assert "https://example.com/new" in user_prompt
    assert user_prompt.index("https://example.com/old") < user_prompt.index(
        "https://example.com/new"
    )
    assert "source_url:" in user_prompt
    assert answer.answer == "Deep answer."
