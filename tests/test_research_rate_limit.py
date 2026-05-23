from __future__ import annotations

import asyncio
import importlib
import sys

import httpx


def _load_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rate-limit.db'}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "site-password")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        return importlib.reload(sys.modules["backend.main"])
    return importlib.import_module("backend.main")


def test_research_rate_limit_returns_429_on_eleventh_request(monkeypatch, tmp_path) -> None:
    main = _load_main(monkeypatch, tmp_path)

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=main.app, client=("198.51.100.10", 12345))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            auth=("pinegraf", "site-password"),
        ) as client:
            responses = [
                await client.post(
                    "/research",
                    json={"question": f"What is item {index}?", "mode": "deep"},
                )
                for index in range(11)
            ]
        return [response.status_code for response in responses]

    statuses = asyncio.run(run())

    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
