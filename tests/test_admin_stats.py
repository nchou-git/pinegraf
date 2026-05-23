from __future__ import annotations

import asyncio
import importlib
import sys

import httpx


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'admin-stats.db'}")
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


def test_admin_stats_returns_pipeline_counts(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    main.store.upsert_profile(name="Jane Doe", class_year="T'24")
    page = main.store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe worked with Pat Person.",
    )
    main.store.mark_raw_page_parsed(page.id)

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            response = await client.get("/admin/stats")
        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(run())

    assert {
        "pages_crawled",
        "pages_parsed",
        "entities",
        "connections",
    }.issubset(payload)
    assert payload["pages_crawled"] == 1
    assert payload["pages_parsed"] == 1
    assert payload["entities"] == 1
    assert payload["connections"] == 0


def test_admin_stats_requires_admin_auth(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> int:
        async with _client(main) as client:
            response = await client.get("/admin/stats")
        return response.status_code

    assert asyncio.run(run()) == 401
