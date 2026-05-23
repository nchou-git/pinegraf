from __future__ import annotations

import asyncio
import importlib
import sys

import httpx

from backend.db.models import Connection
from backend.db.store import Store
from backend.resolution.entity_resolver import reconcile_all


def load_mock_main(monkeypatch, tmp_path, db_name: str = "reconcile-endpoint.db"):
    monkeypatch.setenv("USE_MOCK_EXTRACT", "true")
    monkeypatch.setenv("USE_MOCK_QUERY", "true")
    monkeypatch.setenv("USE_MOCK_FETCH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / db_name}")
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


def seed_reconcile_fixture(store: Store) -> None:
    errik = store.upsert_profile(name="Errik Anderson", class_year="T'07")
    daniella = store.upsert_profile(name="Daniella Reichstetter", class_year="T'07")
    page = store.save_raw_page(
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        source_url="https://example.com/eir",
        page_title="EIR",
        page_text="Errik Anderson partnered with Daniella Reichstetter on Gyrobike.",
    )
    with store.session() as session:
        session.add(
            Connection(
                alum_name="Errik Anderson",
                entity_id=errik.entity_id,
                connected_name="Daniella Reichstetter T'07",
                source_raw_page_id=page.id,
                relationship_type="partnered with to license invention",
                confidence_score=0.9,
                text_evidence="Errik Anderson partnered with Daniella Reichstetter.",
                validation_verdict="keep",
            )
        )
        session.commit()
    assert daniella.entity_id is not None


def graph_state(store: Store) -> dict[str, object]:
    with store.session() as session:
        connections = list(session.query(Connection).order_by(Connection.id.asc()))
        rows = [
            (
                connection.alum_name,
                connection.entity.canonical_name if connection.entity is not None else None,
                (
                    connection.connected_entity.canonical_name
                    if connection.connected_entity is not None
                    else None
                ),
                connection.connected_name,
                connection.relationship_type,
                connection.derivation,
            )
            for connection in connections
        ]
    return {"stats": store.admin_stats(), "connections": rows}


def test_reconcile_endpoint_returns_status(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)
    seed_reconcile_fixture(main.store)

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            response = await client.post("/admin/reconcile/run")
        assert response.status_code == 200
        return response.json()

    payload = asyncio.run(run())

    assert payload["status"] == "ok"
    assert payload["linked"] >= 1
    assert payload["merged"] >= 2


def test_reconcile_endpoint_requires_admin_auth(monkeypatch, tmp_path) -> None:
    main = load_mock_main(monkeypatch, tmp_path)

    async def run() -> int:
        async with _client(main) as client:
            response = await client.post("/admin/reconcile/run")
        return response.status_code

    assert asyncio.run(run()) == 401


def test_reconcile_script_and_endpoint_match_db_state(monkeypatch, tmp_path) -> None:
    script_store = Store(f"sqlite:///{tmp_path / 'script-reconcile.db'}")
    script_store.init_db()
    seed_reconcile_fixture(script_store)
    script_result = reconcile_all(script_store)

    main = load_mock_main(monkeypatch, tmp_path, db_name="endpoint-reconcile.db")
    seed_reconcile_fixture(main.store)

    async def run() -> dict[str, object]:
        async with _client(main) as client:
            login = await client.post("/admin/login", json={"password": "test-password"})
            client.cookies.update(login.cookies)
            response = await client.post("/admin/reconcile/run")
        assert response.status_code == 200
        return response.json()

    endpoint_result = asyncio.run(run())

    assert endpoint_result == script_result.to_dict()
    assert graph_state(main.store) == graph_state(script_store)
