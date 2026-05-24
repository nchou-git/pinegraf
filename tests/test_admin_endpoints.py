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

    async def fake_normalize_run(run_id, *, store):
        del store
        return [uuid.UUID(str(run_id))]

    monkeypatch.setattr(main_module, "start_run", fake_start_run)
    monkeypatch.setattr(main_module, "normalize_run", fake_normalize_run)

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/admin/stats").status_code == 401

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
            "/admin/runs/adhoc",
            headers=admin_headers,
            json={"source_id": source_id, "urls": ["https://example.com/a"]},
        )
        assert run_response.status_code == 200
        run_id = run_response.json()["run_id"]

        get_run_response = client.get(f"/admin/runs/{run_id}", headers=admin_headers)
        assert get_run_response.status_code == 200
        assert get_run_response.json()["kind"] == "adhoc"

        normalize_response = client.post(
            f"/admin/runs/{run_id}/normalize",
            headers=admin_headers,
        )
        assert normalize_response.status_code == 200
        assert normalize_response.json()["document_ids"] == [run_id]

        stats_response = client.get("/admin/stats", headers=admin_headers)
        assert stats_response.status_code == 200
        assert stats_response.json()["sources"] == 3
        assert stats_response.json()["source_runs"] == 1
