from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from backend import main as main_module
from backend.config import get_settings
from backend.db.models import Entity, EntitySummary


def _basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_user_api_requires_site_auth_and_lists_directory(store, monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "site")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()
    with store.session() as session:
        entity = Entity(kind="person", canonical_name="Errik Anderson")
        session.add(entity)
        session.flush()
        session.add(
            EntitySummary(
                entity_id=entity.id,
                display_name="Errik Anderson",
                primary_attributes={"current_employer": "Example"},
                connection_count=2,
                source_count=1,
                confidence_avg=0.91,
            )
        )
        session.commit()

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/api/me").status_code == 401

        headers = _basic("site", "secret")
        me_response = client.get("/api/me", headers=headers)
        assert me_response.status_code == 200
        assert me_response.json()["workspace"]["slug"] == "tuck"

        admin_me_response = client.get("/api/me", headers=_basic("admin", "pinegraf"))
        assert admin_me_response.status_code == 200
        assert admin_me_response.json()["is_admin"] is True

        directory_response = client.get("/api/directory?q=Errik", headers=headers)
        assert directory_response.status_code == 200
        payload = directory_response.json()
        assert payload["total"] == 1
        assert payload["results"][0]["canonical_name"] == "Errik Anderson"


def test_week2_admin_endpoints_require_admin_auth(store, admin_headers, monkeypatch) -> None:
    async def fake_run_full_pipeline(workspace_id, source_run_id, *, store):
        del workspace_id, source_run_id, store
        return set()

    monkeypatch.setattr(main_module, "run_full_pipeline", fake_run_full_pipeline)
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="adhoc",
        spec={"urls": ["https://example.com/story"]},
        triggered_by="test",
    )

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/admin/conflicts").status_code == 401

        conflicts_response = client.get("/admin/conflicts", headers=admin_headers)
        assert conflicts_response.status_code == 200
        assert conflicts_response.json()["results"] == []

        pipeline_response = client.post(f"/admin/runs/{run.id}/pipeline", headers=admin_headers)
        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["status"] == "started"
