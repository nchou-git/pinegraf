from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import httpx


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_SEARCH", "true")
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        module = importlib.reload(sys.modules["backend.main"])
    else:
        module = importlib.import_module("backend.main")
    return module


def test_crawl_parse_and_query_endpoints(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            count_response = await client.get("/alumni-count")
            assert count_response.status_code == 200
            assert count_response.json() == {
                "count": len(main.load_alumni_csv(Path("data/alumni.csv")))
            }

            crawl_start = await client.post("/crawl/start")
            assert crawl_start.status_code == 200
            assert crawl_start.json()["status"] == "started"
            crawl_stream = await client.get("/crawl/stream")
            assert crawl_stream.status_code == 200
            assert '"kind": "crawl_start"' in crawl_stream.text
            assert '"kind": "page_fetched"' in crawl_stream.text
            assert '"kind": "done"' in crawl_stream.text

            parse_start = await client.post("/parse/start")
            assert parse_start.status_code == 200
            assert parse_start.json()["status"] == "started"
            parse_stream = await client.get("/parse/stream")
            assert parse_stream.status_code == 200
            assert '"kind": "parse_start"' in parse_stream.text
            assert '"kind": "page_parsed"' in parse_stream.text
            assert '"kind": "done"' in parse_stream.text

            strict_query = await client.post(
                "/query",
                json={"question": "Who works at Acme Corp?", "mode": "strict"},
            )
            assert strict_query.status_code == 200
            assert "Acme alumni" in strict_query.json()["answer"]

            deep_query = await client.post(
                "/query",
                json={"question": "What pages mention Gyrobike?", "mode": "deep"},
            )
            assert deep_query.status_code == 200
            assert "[source](" in deep_query.json()["answer"]

            profiles_response = await client.get("/profiles")
            assert profiles_response.status_code == 200
            assert profiles_response.json()["profiles"]

    asyncio.run(run_flow())


def test_favicon_endpoint(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/favicon.svg")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/svg+xml")
        assert 'aria-label="Pinegraf logo"' in response.text

    asyncio.run(run_flow())
