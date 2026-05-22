from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import UTC, datetime

import httpx


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")

    from backend.config import get_settings

    get_settings.cache_clear()
    if "backend.main" in sys.modules:
        module = importlib.reload(sys.modules["backend.main"])
    else:
        module = importlib.import_module("backend.main")
    return module


def _client(main) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=main.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# ---------- public endpoints ----------

def test_lookup_returns_results(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    main.store.upsert_profile(name="Jane Doe", class_year="T'24", discovered_via="test")
    main.store.upsert_profile(name="John Smith", class_year="T'23", discovered_via="test")

    async def run() -> None:
        async with _client(main) as client:
            r = await client.post("/lookup", json={"name": "jane"})
            assert r.status_code == 200
            data = r.json()
            assert data["count"] == 1
            assert data["results"][0]["name"] == "Jane Doe"

            r = await client.post("/lookup", json={"class_year": "T'23"})
            assert r.json()["count"] == 1

            r = await client.post("/lookup", json={})
            assert r.json()["count"] == 2

    asyncio.run(run())


def test_research_endpoint(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> None:
        async with _client(main) as client:
            r = await client.post(
                "/research", json={"question": "Anything mentioning Gyrobike?", "mode": "deep"}
            )
            assert r.status_code == 200
            assert r.json()["mode"] == "deep"
            assert "answer" in r.json()

    asyncio.run(run())


def test_lookup_audit_preserves_request_body(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> None:
        async with _client(main) as client:
            r = await client.post("/lookup", json={"name": "Jane"})
            assert r.status_code == 200
            assert "results" in r.json()

    asyncio.run(run())

    events = main.store.list_audit_events(action="lookup")
    assert len(events) == 1
    assert events[0].actor == "anon"
    assert events[0].payload["body"]["name"] == "Jane"


# ---------- admin auth ----------

def test_admin_login_audit_redacts_password(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> httpx.Response:
        async with _client(main) as client:
            return await client.post("/admin/login", json={"password": "test-password"})

    response = asyncio.run(run())
    assert response.status_code == 200
    assert "pinegraf_admin" in response.headers["set-cookie"]

    events = main.store.list_audit_events(action="admin_login")
    assert len(events) == 1
    assert events[0].payload["body"]["password"] == "[redacted]"


def test_admin_login_rejects_bad_password(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> int:
        async with _client(main) as client:
            r = await client.post("/admin/login", json={"password": "wrong"})
            return r.status_code

    assert asyncio.run(run()) in {401, 403}


def test_admin_audit_requires_admin_auth(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> tuple[int, int]:
        async with _client(main) as client:
            denied = await client.get("/admin/audit")
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            allowed = await client.get("/admin/audit")
        return denied.status_code, allowed.status_code

    denied_status, allowed_status = asyncio.run(run())
    assert denied_status == 403
    assert allowed_status == 200


def test_admin_audit_filters(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    main.store.add_audit_event(
        actor="anon",
        action="lookup",
        payload={"method": "POST", "path": "/lookup", "body": {}},
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    main.store.add_audit_event(
        actor="admin",
        action="admin_login",
        payload={"method": "POST", "path": "/admin/login", "body": {}},
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            response = await client.get(
                "/admin/audit",
                params={
                    "since": "2026-01-01T00:00:00Z",
                    "actor": "anon",
                    "action": "lookup",
                },
            )
        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(run())
    assert len(payload["events"]) == 1
    assert payload["events"][0]["actor"] == "anon"
    assert payload["events"][0]["action"] == "lookup"


# ---------- static frontends ----------

def test_favicon_endpoint(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> None:
        async with _client(main) as client:
            r = await client.get("/favicon.svg")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("image/svg+xml")

    asyncio.run(run())


def test_admin_html_served(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> None:
        async with _client(main) as client:
            r = await client.get("/admin")
            assert r.status_code == 200
            assert "Pinegraf admin" in r.text
            r = await client.get("/admin.js")
            assert r.status_code == 200

    asyncio.run(run())
