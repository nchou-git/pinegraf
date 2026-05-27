from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main as main_module
from backend.db.models import AuditLog
from backend.db.store import content_digest


def test_stop_parse_then_start_again_supersedes_old_run_and_skips_parsed_fetches(
    store,
    admin_headers,
    monkeypatch,
) -> None:
    queued: list[tuple[str, str]] = []
    stopped: list[str] = []

    async def execute_cloud_run_job(run_id, mode: str) -> None:
        queued.append((str(run_id), mode))

    def cancel_cloud_run_execution(run) -> str:
        stopped.append(str(run.id))
        return "projects/p/locations/r/jobs/pinegraf-parse/executions/e"

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute_cloud_run_job)
    monkeypatch.setattr(main_module, "cancel_cloud_run_execution", cancel_cloud_run_execution)
    source = store.upsert_source(kind="domain", identifier="resume.example")
    crawl_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="complete",
    )
    parsed_fetch = store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://resume.example/parsed",
        body_bytes=b"parsed",
        http_status=200,
    )
    unparsed_fetch = store.add_fetch(
        source_run_id=crawl_run.id,
        url="https://resume.example/unparsed",
        body_bytes=b"unparsed",
        http_status=200,
    )
    document = store.create_document_with_chunks(
        content_hash=content_digest(b"parsed"),
        cleaned_text="parsed",
        title="Parsed",
        canonical_url="https://resume.example/parsed",
        language="en",
        word_count=1,
        first_seen_fetch_id=parsed_fetch.id,
        chunks=[("parsed", 1, None)],
    )
    store.link_document_fetch(document.id, parsed_fetch.id)
    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        stop = client.post(f"/admin/runs/{parse_run.id}/stop", headers=admin_headers)
        restart = client.post(f"/admin/sources/{source.id}/parse", headers=admin_headers)

    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"
    assert stopped == [str(parse_run.id)]
    assert restart.status_code == 200
    new_run_id = restart.json()["run_id"]
    assert queued == [(new_run_id, "parse")]
    assert store.get_source_run(parse_run.id).status == "superseded"
    assert store.get_source_run(uuid.UUID(new_run_id)).status == "queued"
    assert store.pending_fetch_ids(source_id=source.id) == [unparsed_fetch.id]
    with store.session() as session:
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "run.superseded_by_resume")
        ).scalar_one()
    assert audit.target_id == new_run_id
    assert audit.payload["old_run_id"] == str(parse_run.id)


def test_stop_crawl_while_parse_runs_only_affects_crawl(
    store,
    admin_headers,
    monkeypatch,
) -> None:
    stopped: list[str] = []

    def cancel_cloud_run_execution(run) -> str:
        stopped.append(str(run.id))
        return "projects/p/locations/r/jobs/pinegraf-crawl/executions/e"

    monkeypatch.setattr(main_module, "cancel_cloud_run_execution", cancel_cloud_run_execution)
    source = store.upsert_source(kind="domain", identifier="concurrent.example")
    crawl_run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={"source_id": str(source.id), "source_input": source.identifier},
        triggered_by="test",
        status="running",
    )
    parse_run = store.create_source_run(
        source_id=source.id,
        kind="parse",
        spec={"source_id": str(source.id), "scope": "unparsed"},
        triggered_by="test",
        status="running",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(f"/admin/runs/{crawl_run.id}/stop", headers=admin_headers)

    assert response.status_code == 200
    assert stopped == [str(crawl_run.id)]
    assert store.get_source_run(crawl_run.id).status == "stopped"
    assert store.get_source_run(parse_run.id).status == "running"
