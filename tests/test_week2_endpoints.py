from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main as main_module
from backend.db.models import Entity, EntitySummary, SourceRun
from backend.web_api import list_sources


def test_user_api_is_public_and_lists_directory(store) -> None:
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
        me_response = client.get("/api/me")
        assert me_response.status_code == 200
        assert me_response.json()["workspace"]["slug"] == "tuck"
        assert me_response.json()["is_admin"] is False

        directory_response = client.get("/api/directory?q=Errik")
        assert directory_response.status_code == 200
        payload = directory_response.json()
        assert payload["total"] == 1
        assert payload["results"][0]["canonical_name"] == "Errik Anderson"


def test_source_coverage_separates_pages_fetched_from_documents_parsed(store) -> None:
    source = store.upsert_source(kind="domain", identifier="coverage.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    store.add_fetch(
        source_run_id=run.id,
        url="https://coverage.example/page",
        body_bytes=b"<html>ok</html>",
        http_status=200,
    )

    [payload] = list_sources(store)

    assert payload["coverage"]["pages_fetched"] == 1
    assert payload["coverage"]["documents_parsed"] == 0
    assert payload["coverage"]["documents"] == 0


def test_week2_admin_endpoints_require_admin_auth(store, admin_headers, monkeypatch) -> None:
    from backend.jobs import run as jobs_run

    async def fake_run_full_pipeline(source_run_id, *, store, progress_run_id=None):
        del source_run_id, store, progress_run_id
        return set()

    monkeypatch.setattr(jobs_run, "run_full_pipeline", fake_run_full_pipeline)

    async def fake_execute_cloud_run_job(run_id, mode: str) -> None:
        assert mode == "pipeline"
        monkeypatch.setenv("PINEGRAF_RUN_ID", str(run_id))
        monkeypatch.setenv("PINEGRAF_MODE", mode)
        await jobs_run.run_from_env(store=store)

    monkeypatch.setattr(main_module, "execute_cloud_run_job", fake_execute_cloud_run_job)
    source = store.upsert_source(kind="domain", identifier="example.com")
    store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"urls": ["https://example.com/story"]},
        triggered_by="test",
        status="complete",
    )

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/admin/conflicts").status_code == 401

        conflicts_response = client.get("/admin/conflicts", headers=admin_headers)
        assert conflicts_response.status_code == 200
        assert conflicts_response.json()["results"] == []

        pipeline_response = client.post(f"/admin/sources/{source.id}/parse", headers=admin_headers)
        assert pipeline_response.status_code == 200
        assert pipeline_response.json()["status"] == "queued"

    with store.session() as session:
        pipeline_run = session.execute(
            select(SourceRun).where(SourceRun.source_id == source.id, SourceRun.kind == "pipeline")
        ).scalar_one()
    assert pipeline_run.spec["pipeline_source_run_id"]
