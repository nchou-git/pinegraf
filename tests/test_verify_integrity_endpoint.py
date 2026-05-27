from __future__ import annotations

from fastapi.testclient import TestClient

from backend import main as main_module


def test_verify_integrity_clean_source_returns_ok(store, admin_headers) -> None:
    source = store.upsert_source(kind="domain", identifier="clean.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://clean.example/page",
        body_bytes=b"body",
        http_status=200,
    )
    document = store.create_document_with_chunks(
        content_hash=b"c" * 32,
        cleaned_text="body",
        title=None,
        canonical_url=fetch.url,
        language="en",
        word_count=1,
        first_seen_fetch_id=fetch.id,
        chunks=[("body", 1, None)],
    )
    store.link_document_fetch(document.id, fetch.id)
    store.refresh_source_crawl_counters(source.id)

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/sources/{source.id}/verify-integrity",
            headers=admin_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert all(value == 0 for value in payload["violations"].values())


def test_verify_integrity_reports_missing_documentfetch_link(store, admin_headers) -> None:
    source = store.upsert_source(kind="domain", identifier="missing-link.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    store.add_fetch(
        source_run_id=run.id,
        url="https://missing-link.example/page",
        body_bytes=b"body",
        http_status=200,
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/sources/{source.id}/verify-integrity",
            headers=admin_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["fetches_missing_documentfetch_link"] == 1
    assert payload["fetches_missing_documentfetch_urls"] == ["https://missing-link.example/page"]
