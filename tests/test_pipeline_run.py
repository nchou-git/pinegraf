from __future__ import annotations

import asyncio
import importlib
import sys

import httpx


def load_mock_main(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'pipeline-run.db'}")
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PINEGRAF_ADMIN_COOKIE_SECRET", "test-secret")
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "site-password")
    monkeypatch.setenv("CRAWL_SEED_URLS", "https://example.com/errik-anderson")
    monkeypatch.setenv("CRAWL_ALLOWED_DOMAINS", "example.com")
    monkeypatch.setenv("CRAWL_MAX_PAGES", "1")

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
        timeout=20.0,
    )


async def _drain_stream(client: httpx.AsyncClient, path: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    async with client.stream("GET", path) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            import json

            payload = json.loads(line.removeprefix("data: "))
            events.append(payload)
            if payload.get("kind") == "done":
                break
    return events


def test_full_mock_pipeline_marks_pipeline_run_complete(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)

            run_start = await client.post("/admin/pipeline/run/start")
            assert run_start.status_code == 200
            run_payload = run_start.json()

            crawl_start = await client.post("/admin/crawl/start")
            assert crawl_start.status_code == 200
            crawl_events = await _drain_stream(client, "/admin/crawl/stream")
            assert crawl_events[-1]["kind"] == "done"
            assert not crawl_events[-1].get("error")

            parse_start = await client.post("/admin/parse/start", json={})
            assert parse_start.status_code == 200
            parse_events = await _drain_stream(client, "/admin/parse/stream")
            assert parse_events[-1]["kind"] == "done"
            assert not parse_events[-1].get("error")

            reconcile = await client.post("/admin/reconcile/run")
            assert reconcile.status_code == 200

            audit = await client.post("/admin/audit/run", json={"sample_size": 1})
            assert audit.status_code == 200

            finish = await client.post(
                f"/admin/pipeline/run/{run_payload['id']}/finish",
                json={"status": "complete", "error_message": ""},
            )
            assert finish.status_code == 200
            return finish.json()

    payload = asyncio.run(run())

    assert payload["status"] == "complete"
    assert payload["finished_at"] is not None
    latest = main.store.latest_pipeline_run()
    assert latest is not None
    assert latest.status == "complete"
    assert latest.finished_at is not None
