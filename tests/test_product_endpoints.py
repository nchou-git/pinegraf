from __future__ import annotations

import asyncio
import importlib
import json
import sys

import httpx


def load_mock_main(monkeypatch, tmp_path, db_name: str = "product.db"):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / db_name}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "site-password")
    monkeypatch.setenv("GIT_SHA", "test-sha")
    monkeypatch.setenv("DEPLOYED_AT", "2026-05-23T00:00:00Z")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        return importlib.reload(sys.modules["backend.main"])
    return importlib.import_module("backend.main")


def _client(main, *, auth: bool = True, ip: str = "203.0.113.10") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=main.app, client=(ip, 12345))
    kwargs: dict[str, object] = {"transport": transport, "base_url": "http://test"}
    if auth:
        kwargs["auth"] = ("pinegraf", "site-password")
    return httpx.AsyncClient(**kwargs)


def test_public_stats_and_version_require_site_auth(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> tuple[int, dict[str, object], int, dict[str, object]]:
        async with _client(main, auth=False) as anon:
            denied = await anon.get("/stats")
            version_denied = await anon.get("/version")
        async with _client(main) as client:
            stats = await client.get("/stats")
            version = await client.get("/version")
        assert stats.status_code == 200
        assert version.status_code == 200
        return denied.status_code, stats.json(), version_denied.status_code, version.json()

    stats_denied, stats, version_denied, version = asyncio.run(run())

    assert stats_denied == 401
    assert {"alumni", "pages_crawled", "connections"}.issubset(stats)
    assert version_denied == 401
    assert version == {"git_sha": "test-sha", "deployed_at": "2026-05-23T00:00:00Z"}


def test_lookup_paginates_server_side(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    for index in range(30):
        main.store.upsert_profile(name=f"Person {index:02d}", class_year="T'24")

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            response = await client.post("/lookup?offset=25&limit=25", json={})
        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(run())

    assert payload["count"] == 30
    assert payload["offset"] == 25
    assert payload["returned"] == 5
    assert len(payload["results"]) == 5


def test_research_stream_returns_sse_and_requires_site_auth(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> tuple[int, list[dict[str, object]]]:
        async with _client(main, auth=False) as anon:
            denied = await anon.post(
                "/research/stream",
                json={"question": "Anything mentioning Gyrobike?", "mode": "deep"},
            )
        events: list[dict[str, object]] = []
        async with _client(main) as client:
            async with client.stream(
                "POST",
                "/research/stream",
                json={"question": "Anything mentioning Gyrobike?", "mode": "deep"},
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line.removeprefix("data: ")))
        return denied.status_code, events

    denied_status, events = asyncio.run(run())

    assert denied_status == 401
    assert any(event["kind"] == "token" for event in events)
    assert events[-1]["kind"] == "done"


def test_admin_db_and_reset_extraction(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    profile = main.store.upsert_profile(name="Jane Doe", class_year="T'24")
    main.store.save_raw_page(
        alum_name="Jane Doe",
        entity_id=profile.entity_id,
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Jane Doe works at Acme.",
    )

    async def run() -> tuple[int, int, dict[str, object], int, dict[str, object]]:
        async with _client(main) as anon:
            denied = await anon.get("/admin/db")
            reset_denied = await anon.post(
                "/admin/reset/extraction",
                json={"confirmation": "RESET"},
            )
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            db = await client.get("/admin/db")
            reset = await client.post("/admin/reset/extraction", json={"confirmation": "RESET"})
        assert db.status_code == 200
        assert reset.status_code == 200
        return (
            denied.status_code,
            reset_denied.status_code,
            db.json(),
            reset.status_code,
            reset.json(),
        )

    denied_status, reset_denied_status, db_payload, reset_status, reset_payload = asyncio.run(run())

    assert denied_status == 401
    assert reset_denied_status == 401
    assert db_payload["tables"]["raw_pages"] == 1
    assert reset_status == 200
    assert reset_payload["status"] == "ok"
    assert main.store.admin_stats()["pages_crawled"] == 0
    assert main.store.admin_stats()["entities"] == 1


def test_user_global_rate_limit(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path, db_name="rate-global.db")

    async def run() -> list[int]:
        async with _client(main, ip="198.51.100.99") as client:
            responses = [await client.get("/stats") for _ in range(61)]
        return [response.status_code for response in responses]

    statuses = asyncio.run(run())

    assert statuses[:60] == [200] * 60
    assert statuses[60] == 429
