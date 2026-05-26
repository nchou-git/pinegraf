from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend import web_api
from backend.config import get_settings
from backend.db.store import content_digest


class FakeStream:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = iter(tokens)

    def __aiter__(self) -> "FakeStream":
        return self

    async def __anext__(self):
        try:
            token = next(self._tokens)
        except StopIteration as exc:
            raise StopAsyncIteration from exc
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=token))])


@pytest.mark.asyncio
async def test_ask_stream_uses_openai_stream_and_caches_final_answer(
    store,
    monkeypatch,
) -> None:
    web_api._ASK_CACHE.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(web_api, "embed_texts", _fake_embed_texts)

    create_calls: list[dict[str, object]] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            create_calls.append(kwargs)
            return FakeStream(["Hello", " world"])

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-key"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(web_api, "AsyncOpenAI", FakeClient)
    source = store.upsert_source(kind="domain", identifier="ask.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://ask.example/page",
        body_bytes=b"<html>Errik Anderson founded Example.</html>",
        http_status=200,
    )
    store.create_document_with_chunks(
        content_hash=content_digest(b"Errik Anderson founded Example."),
        cleaned_text="Errik Anderson founded Example.",
        title="Ask",
        canonical_url="https://ask.example/page",
        language="en",
        word_count=4,
        first_seen_fetch_id=fetch.id,
        chunks=[("Errik Anderson founded Example.", 4, None)],
    )

    first_events = await _collect_ask_events(store)
    second_events = await _collect_ask_events(store)

    assert create_calls[0]["stream"] is True
    assert [event["text"] for event in first_events if event["kind"] == "token"] == [
        "Hello",
        " world",
    ]
    assert [event["text"] for event in second_events if event["kind"] == "token"] == [
        "Hello world"
    ]
    assert len(create_calls) == 1


async def _fake_embed_texts(_texts):
    return []


async def _collect_ask_events(store):
    events = []
    async for chunk in web_api.ask_stream(store, question="Who founded Example?"):
        payload = chunk.decode("utf-8").removeprefix("data: ").strip()
        events.append(json.loads(payload))
    return events
