from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import main as main_module
from backend.config import get_settings
from backend.db.models import Entity, EntityNeighborhood, EntitySummary


def test_gated_data_endpoints_require_admin(admin_headers, monkeypatch) -> None:
    async def fake_ask_stream(*args, **kwargs) -> AsyncIterator[bytes]:
        del args, kwargs
        yield b'data: {"kind":"done"}\n\n'

    monkeypatch.setenv("PINEGRAF_DEMO_MODE", "false")
    monkeypatch.setattr(main_module, "engine_pool_config", lambda _engine: {})
    monkeypatch.setattr(main_module, "_warn_if_empty_database_since_deploy", lambda _store: None)
    monkeypatch.setattr(main_module, "append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "stats", lambda _store: {})
    monkeypatch.setattr(main_module, "list_claims", lambda _store, **kwargs: {"claims": []})
    monkeypatch.setattr(main_module, "ask_stream", fake_ask_stream)

    app_store = SimpleNamespace(engine=object())
    with TestClient(main_module.create_app(app_store)) as client:
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is False

        for path in ("/api/claims", "/api/stats"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=admin_headers).status_code == 200

        ask_payload = {"question": "Who founded Example?"}
        assert client.post("/api/ask", json=ask_payload).status_code == 401
        assert client.post("/api/ask", json=ask_payload, headers=admin_headers).status_code == 200


def test_graph_reads_are_public_but_other_data_apis_stay_gated(store, monkeypatch) -> None:
    monkeypatch.setenv("BASIC_AUTH_CREDENTIALS", "demo:secret")
    get_settings.cache_clear()

    with store.session() as session:
        person = Entity(kind="person", canonical_name="Ada Lovelace")
        org = Entity(kind="org", canonical_name="Analytical Engines")
        session.add_all([person, org])
        session.flush()
        session.add(
            EntitySummary(
                entity_id=person.id,
                display_name=person.canonical_name,
                primary_attributes={"current_title": "Founder"},
                connection_count=1,
                source_count=1,
            )
        )
        session.add(
            EntityNeighborhood(
                entity_id=person.id,
                neighbor_id=org.id,
                predicates=["founded"],
                evidence_count=1,
            )
        )
        session.commit()
        person_id = person.id

    with TestClient(main_module.create_app(store)) as client:
        search = client.get("/api/entities/search?q=Ada")
        assert search.status_code == 200
        assert search.json()["results"][0]["canonical_name"] == "Ada Lovelace"

        entity = client.get(f"/api/entity/{person_id}")
        assert entity.status_code == 200
        entity_payload = entity.json()
        assert entity_payload["identity"]["canonical_name"] == "Ada Lovelace"
        assert entity_payload["connections"][0]["neighbor_name"] == "Analytical Engines"

        graph = client.get(f"/api/graph?focus={person_id}&depth=1")
        assert graph.status_code == 200
        assert graph.json()["identity"]["entity_id"] == str(person_id)

        assert client.get("/api/claims").status_code == 401
