from __future__ import annotations

import base64
import uuid

from fastapi.testclient import TestClient

from backend import main as main_module


def test_admin_auth_required_and_happy_paths(
    store,
    admin_headers,
    fake_httpx,
    run_jobs_inline,
) -> None:
    fake_httpx.responses = {
        "https://example.com/robots.txt": fake_httpx.Response(
            "https://example.com/robots.txt",
            200,
            b"User-agent: *\nAllow: /\n",
        ),
        "https://example.com/": fake_httpx.Response(
            "https://example.com/",
            200,
            b"<html>Example</html>",
        ),
    }
    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/api/logs/stream").status_code == 403
        assert client.get("/admin/conflicts").status_code == 401
        stale_token = base64.b64encode(b"admin:Pinegrafposen$").decode("ascii")
        assert (
            client.get(
                "/admin/conflicts",
                headers={"Authorization": f"Basic {stale_token}"},
            ).status_code
            == 401
        )

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
        assert run_response.json()["status"] == "queued"

        counts = store.table_counts(["sources", "source_runs"])
        assert counts["sources"] == 1
        assert counts["source_runs"] == 1
        run_id = uuid.UUID(run_response.json()["run_id"])
        assert store.get_source_run(run_id).status == "complete"


def test_crawl_rejects_existing_active_source_run(store, admin_headers, monkeypatch) -> None:
    called = False

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        nonlocal called
        called = True
        del run_id, mode

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    existing = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="queued",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(f"/admin/sources/{source.id}/crawl", headers=admin_headers)

    assert response.status_code == 409
    assert response.json() == {
        "error": "already_running",
        "run_id": str(existing.id),
        "status": "queued",
    }
    assert called is False
    assert store.table_counts(["source_runs"])["source_runs"] == 1


def test_parse_rejects_existing_active_source_run(store, admin_headers, monkeypatch) -> None:
    called = False

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        nonlocal called
        called = True
        del run_id, mode

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    complete = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    existing = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(f"/admin/sources/{source.id}/parse", headers=admin_headers)

    assert response.status_code == 409
    assert response.json() == {
        "error": "already_running",
        "run_id": str(existing.id),
        "status": "running",
    }
    assert called is False
    assert store.get_source_run(complete.id).status == "complete"
    assert store.get_source_run(existing.id).status == "running"


def test_admin_login_page_uses_single_account_and_password_toggle(store) -> None:
    with TestClient(main_module.create_app(store)) as client:
        form = client.get("/admin/login")
        assert form.status_code == 200
        assert "<title>Pinegraf</title>" in form.text
        assert "login-subtitle" not in form.text
        assert 'value="pinegraf"' in form.text
        assert "togglePasswordVisibility" in form.text

        stale_login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "Pinegrafposen$"},
            follow_redirects=False,
        )
        assert stale_login.status_code == 401

        login = client.post(
            "/admin/login",
            data={"username": "pinegraf", "password": "Pinegrafposen$"},
            follow_redirects=False,
        )
        assert login.status_code == 303
