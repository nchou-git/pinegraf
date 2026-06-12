from __future__ import annotations

import base64
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main as main_module
from backend.admin_session import COOKIE_NAME, issue
from backend.config import get_settings
from backend.db.models import AuditLog, SourceRun


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
        unauthorized = client.get("/admin/conflicts")
        assert unauthorized.status_code == 401
        assert "www-authenticate" not in unauthorized.headers
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


def test_admin_create_source_accepts_depth_limited_and_full_crawl_sources(
    store,
    admin_headers,
) -> None:
    with TestClient(main_module.create_app(store)) as client:
        first = client.post(
            "/admin/sources",
            headers=admin_headers,
            json={
                "kind": "domain",
                "identifier": "https://tuck.dartmouth.edu/page-a",
                "display_name": "Tuck",
                "crawl_depth": 1,
            },
        )
        second = client.post(
            "/admin/sources",
            headers=admin_headers,
            json={
                "kind": "domain",
                "identifier": "https://tuck.dartmouth.edu/page-b",
                "display_name": "Tuck",
                "crawl_depth": 2,
            },
        )
        full = client.post(
            "/admin/sources",
            headers=admin_headers,
            json={
                "kind": "domain",
                "identifier": "tuck.dartmouth.edu",
                "display_name": "Tuck",
                "crawl_depth": None,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert full.status_code == 200
    assert first.json()["identifier"] == "https://tuck.dartmouth.edu/page-a"
    assert second.json()["identifier"] == "https://tuck.dartmouth.edu/page-b"
    assert full.json()["identifier"] == "tuck.dartmouth.edu"
    assert first.json()["crawl_depth"] == 1
    assert second.json()["crawl_depth"] == 2
    assert full.json()["crawl_depth"] is None
    assert first.json()["display_name"] == second.json()["display_name"] == "Tuck"
    assert store.table_counts(["sources"])["sources"] == 3


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


def test_parse_allows_existing_active_crawl(store, admin_headers, monkeypatch) -> None:
    queued = []

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((run_id, mode))

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    complete = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    crawl = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(f"/admin/sources/{source.id}/parse", headers=admin_headers)

    assert response.status_code == 200
    assert store.get_source_run(complete.id).status == "complete"
    assert store.get_source_run(crawl.id).status == "running"
    assert queued[0][1] == "parse"


def test_parse_rejects_existing_active_parse(store, admin_headers, monkeypatch) -> None:
    called = False

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        nonlocal called
        called = True
        del run_id, mode

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    existing = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
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


def test_parse_accepts_fetch_id_scope_and_audits(store, admin_headers, monkeypatch) -> None:
    queued = []

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((run_id, mode))

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://example.com/page",
        body_bytes=b"<html>ok</html>",
        http_status=200,
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/sources/{source.id}/parse",
            headers=admin_headers,
            json={"scope": "fetch_ids", "fetch_ids": [str(fetch.id)]},
        )
        audit_response = client.get("/admin/audit", headers=admin_headers)

    assert response.status_code == 200
    with store.session() as session:
        parse_run = session.execute(
            select(SourceRun).where(SourceRun.source_id == source.id, SourceRun.kind == "parse")
        ).scalar_one()
    assert parse_run.spec["scope"] == "fetch_ids"
    assert parse_run.spec["fetch_ids"] == [str(fetch.id)]
    assert "parse_source_run_id" not in parse_run.spec
    assert queued == [(parse_run.id, "parse")]
    assert audit_response.json()["entries"][0]["payload"]["fetch_ids_count"] == 1


def test_delete_source_rejects_active_run(store, admin_headers) -> None:
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.delete(f"/admin/sources/{source.id}", headers=admin_headers)

    assert response.status_code == 409
    assert "stop the run first" in response.json()["detail"]
    assert store.get_source(source.id) is not None
    assert store.get_source_run(run.id).status == "running"


def test_stop_run_marks_stopped_and_audits(store, admin_headers, monkeypatch) -> None:
    stopped = []

    def cancel_cloud_run_execution(run) -> str:
        stopped.append(run.id)
        return "projects/p/locations/r/jobs/pinegraf-crawl/executions/e"

    monkeypatch.setattr(main_module, "cancel_cloud_run_execution", cancel_cloud_run_execution)
    source = store.upsert_source(kind="domain", identifier="example.com")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(f"/admin/runs/{run.id}/stop", headers=admin_headers)
        assert store.get_source(source.id).status == "active"
        assert store.get_source_run(run.id).status == "stopped"
        delete_response = client.delete(f"/admin/sources/{source.id}", headers=admin_headers)
        audit_response = client.get("/admin/audit", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert response.json()["cloud_execution_cancelled"] is True
    assert stopped == [run.id]
    assert delete_response.status_code == 200
    assert store.get_source(source.id) is None
    assert audit_response.status_code == 200
    assert [entry["action"] for entry in audit_response.json()["entries"][:2]] == [
        "source.delete",
        "run.stop",
    ]


def test_source_create_and_update_are_audited(store, admin_headers) -> None:
    with TestClient(main_module.create_app(store)) as client:
        create = client.post(
            "/admin/sources",
            headers=admin_headers,
            json={"kind": "domain", "identifier": "example.com"},
        )
        source_id = create.json()["id"]
        update = client.patch(
            f"/admin/sources/{source_id}",
            headers=admin_headers,
            json={"display_name": "Example"},
        )

    assert create.status_code == 200
    assert update.status_code == 200
    with store.session() as session:
        actions = list(
            session.execute(select(AuditLog.action).order_by(AuditLog.ts.asc())).scalars()
        )
    assert actions == ["source.create", "source.update"]


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


def test_admin_login_rejects_invalid_json(store) -> None:
    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            "/admin/login",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid JSON body for admin login."

        response = client.post(
            "/admin/login",
            json=["pinegraf", "Pinegrafposen$"],
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Admin login JSON body must be an object."


def test_basic_auth_wall_uses_demo_login_page_and_sets_admin_session(monkeypatch) -> None:
    monkeypatch.setenv("BASIC_AUTH_CREDENTIALS", "pinegraf:Pinegrafposen$")
    monkeypatch.setenv("SECURE_COOKIES", "false")
    get_settings.cache_clear()
    client = TestClient(main_module.create_app())
    try:
        html_response = client.get("/", headers={"Accept": "text/html"})
        assert html_response.status_code == 200
        assert "WWW-Authenticate" not in html_response.headers
        assert html_response.headers["content-type"].startswith("text/html")
        assert "<title>Pinegraf</title>" in html_response.text
        assert "Demo environment" in html_response.text

        api_response = client.get("/api/anything", headers={"Accept": "application/json"})
        assert api_response.status_code == 401
        assert "WWW-Authenticate" not in api_response.headers
        assert api_response.headers["content-type"].startswith("application/json")
        assert api_response.json() == {"error": "unauthorized"}

        admin_response = client.get("/admin/sources", headers={"Accept": "text/html"})
        assert admin_response.status_code == 401
        assert "WWW-Authenticate" not in admin_response.headers
        assert admin_response.headers["content-type"].startswith("application/json")
        assert admin_response.json() == {"error": "unauthorized"}

        json_accept_response = client.get("/", headers={"Accept": "application/json"})
        assert json_accept_response.status_code == 401
        assert "WWW-Authenticate" not in json_accept_response.headers
        assert json_accept_response.headers["content-type"].startswith("application/json")

        basic_token = base64.b64encode(b"pinegraf:wrong").decode("ascii")
        curl_response = client.get(
            "/non-api",
            headers={"Authorization": f"Basic {basic_token}", "Accept": "text/plain"},
        )
        assert curl_response.status_code == 401
        assert curl_response.headers["WWW-Authenticate"] == "Basic"
        assert curl_response.json() == {"error": "unauthorized"}

        failed_login = client.post(
            "/demo-login",
            json={"username": "pinegraf", "password": "wrong"},
        )
        assert failed_login.status_code == 401
        assert failed_login.json() == {"error": "Invalid credentials"}

        login = client.post(
            "/demo-login",
            json={"username": "pinegraf", "password": "Pinegrafposen$"},
        )
        assert login.status_code == 200
        assert "demo_session=" in login.headers["set-cookie"]
        assert "pg_admin=" in login.headers["set-cookie"]

        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is True

        client.cookies.clear()
        logged_out = client.get("/", headers={"Accept": "text/html"})
        assert logged_out.status_code == 200
        assert "WWW-Authenticate" not in logged_out.headers
    finally:
        get_settings.cache_clear()


def test_demo_index_forces_login_only_for_anonymous_visitors(monkeypatch) -> None:
    monkeypatch.setenv("PINEGRAF_DEMO_MODE", "true")
    get_settings.cache_clear()
    client = TestClient(main_module.create_app(object()))
    anonymous = client.get("/")
    assert anonymous.status_code == 200
    assert "window.__PINEGRAF_FORCE_LOGIN__ = true" in anonymous.text

    client.cookies.set(COOKIE_NAME, issue())
    admin = client.get("/")
    assert admin.status_code == 200
    assert "window.__PINEGRAF_FORCE_LOGIN__ = true" not in admin.text


def test_prod_index_does_not_force_login(monkeypatch) -> None:
    monkeypatch.setenv("PINEGRAF_DEMO_MODE", "false")
    get_settings.cache_clear()
    client = TestClient(main_module.create_app(object()))
    anonymous = client.get("/")
    assert anonymous.status_code == 200
    assert "window.__PINEGRAF_FORCE_LOGIN__ = true" not in anonymous.text

    client.cookies.set(COOKIE_NAME, issue())
    admin = client.get("/")
    assert admin.status_code == 200
    assert "window.__PINEGRAF_FORCE_LOGIN__ = true" not in admin.text


def test_demo_env_enables_demo_index_login_gate(monkeypatch) -> None:
    monkeypatch.delenv("PINEGRAF_DEMO_MODE", raising=False)
    monkeypatch.setenv("PINEGRAF_ENV", "demo")
    get_settings.cache_clear()
    client = TestClient(main_module.create_app(object()))

    response = client.get("/")

    assert response.status_code == 200
    assert "window.__PINEGRAF_FORCE_LOGIN__ = true" in response.text
