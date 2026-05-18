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


def test_enrich_then_query_flow(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            enrich_response = await client.post("/enrich")
            assert enrich_response.status_code == 200
            assert enrich_response.json()["enriched_count"] == len(
                main.load_alumni_csv(Path("data/alumni.csv"))
            )

            query_response = await client.post(
                "/query", json={"question": "Who works at Acme Corp?"}
            )
            assert query_response.status_code == 200
            assert "Acme alumni" in query_response.json()["answer"]

            profiles_response = await client.get("/profiles")
            assert profiles_response.status_code == 200
            assert profiles_response.json()["profiles"]

    asyncio.run(run_flow())


def test_projects_endpoint(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    main.store.add_projects(
        "Errik Anderson",
        [
            {
                "name": "Gyrobike FYP",
                "description": "First-year project",
                "source_url": "https://example.com/gyrobike",
            }
        ],
    )

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/projects")

        assert response.status_code == 200
        assert response.json()["projects"][0]["project_name"] == "Gyrobike FYP"

    asyncio.run(run_flow())


def test_alumni_count_endpoint(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run_flow() -> None:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/alumni-count")

        assert response.status_code == 200
        assert response.json() == {"count": len(main.load_alumni_csv(Path("data/alumni.csv")))}

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
