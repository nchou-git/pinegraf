from __future__ import annotations

import asyncio
import importlib
import sys
import time

import httpx


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'usage-live.db'}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "site-password")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        return importlib.reload(sys.modules["backend.main"])
    return importlib.import_module("backend.main")


def _client(main) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=main.app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        auth=("pinegraf", "site-password"),
    )


def test_admin_usage_live_requires_admin_cookie(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> int:
        async with _client(main) as client:
            response = await client.get("/admin/usage/live")
        return response.status_code

    assert asyncio.run(run()) == 401


def test_admin_usage_live_returns_zeros_when_empty(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            started = time.perf_counter()
            response = await client.get("/admin/usage/live")
            elapsed_ms = (time.perf_counter() - started) * 1000
        assert response.status_code == 200
        assert elapsed_ms < 50
        return response.json()

    payload = asyncio.run(run())

    assert payload == {
        "totals": {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "dollars": 0.0,
        },
        "by_model": {},
    }


def test_admin_usage_live_returns_totals_and_model_costs(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    main.store.record_llm_usage(
        model="gpt-5.4-mini",
        prompt_tokens=100,
        completion_tokens=50,
        dollars=0.01,
        purpose="test",
    )
    main.store.record_llm_usage(
        model="gpt-5.4",
        prompt_tokens=300,
        completion_tokens=100,
        dollars=0.07,
        purpose="test",
    )

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            response = await client.get("/admin/usage/live")
        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(run())

    assert payload["totals"] == {
        "calls": 2,
        "prompt_tokens": 400,
        "completion_tokens": 150,
        "total_tokens": 550,
        "dollars": 0.08,
    }
    assert payload["by_model"] == {"gpt-5.4": 0.07, "gpt-5.4-mini": 0.01}
