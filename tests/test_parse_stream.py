from __future__ import annotations

import asyncio
import importlib
import json
import sys
import uuid

from backend.db.store import Store
from backend.pipeline.crawler import ProgressEvent
from backend.pipeline.parser import ChunkExtraction, ExtractedClaim, _json_model_dump


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'parse-stream.db'}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "site-password")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        return importlib.reload(sys.modules["backend.main"])
    return importlib.import_module("backend.main")


def test_parse_sse_event_serializes_uuid_payload(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    entity_id = uuid.uuid4()
    job = main.StageJob("parse")
    job.queue.put(ProgressEvent("chunk_done", {"entity_id": entity_id, "overall_done": 1}))
    job.queue.put(main.DONE_SENTINEL)

    async def run() -> list[bytes]:
        return [payload async for payload in main._event_generator(job)]

    payloads = asyncio.run(run())

    assert len(payloads) == 1
    decoded = payloads[0].decode("utf-8")
    assert str(entity_id) in decoded
    assert json.loads(decoded.removeprefix("data: ").strip())["entity_id"] == str(entity_id)


def test_extraction_cache_payload_serializes_uuid_fields(tmp_path) -> None:
    entity_id = uuid.uuid4()
    extraction = ChunkExtraction(
        claims=[
            ExtractedClaim(
                subject_name="Errik Anderson",
                predicate="worked_with",
                object_name="Daniella Reichstetter",
                text_evidence="Errik Anderson worked with Daniella Reichstetter.",
                subject_entity_id=entity_id,
            )
        ]
    )
    payload = _json_model_dump(extraction)
    encoded = json.dumps(payload)

    store = Store(f"sqlite:///{tmp_path / 'cache.db'}")
    store.init_db()
    store.set_extraction_cache(
        chunk_sha256="abc123",
        prompt_version="test",
        model="mock-model",
        response_json=payload,
    )

    assert str(entity_id) in encoded
    assert (
        store.get_extraction_cache(
            chunk_sha256="abc123",
            prompt_version="test",
            model="mock-model",
        )
        == payload
    )
