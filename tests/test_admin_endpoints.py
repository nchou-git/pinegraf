from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from backend import main as main_module


def test_admin_auth_required_and_happy_paths(store, admin_headers, monkeypatch) -> None:
    async def fake_start_run(kind: str, spec: dict[str, object], triggered_by: str, *, store):
        return store.create_source_run(
            source_id=uuid.UUID(str(spec["source_id"])),
            kind=kind,
            spec=spec,
            triggered_by=triggered_by,
        ).id

    monkeypatch.setattr(main_module, "start_run", fake_start_run)

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/admin/conflicts").status_code == 401

        source_response = client.post(
            "/admin/sources",
            headers=admin_headers,
            json={
                "kind": "domain",
                "identifier": "example.com",
                "trust_weight": 0.8,
                "display_name": "Example",
            },
        )
        assert source_response.status_code == 200
        source_id = source_response.json()["id"]

        run_response = client.post(
            f"/admin/sources/{source_id}/crawl",
            headers=admin_headers,
        )
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "started"

        counts = store.table_counts(["sources", "source_runs"])
        assert counts["sources"] == 1
        assert counts["source_runs"] == 1
