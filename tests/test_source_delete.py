from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main as main_module
from backend.config import get_settings
from backend.db.models import (
    Chunk,
    Claim,
    ClaimEvidence,
    ClaimRaw,
    Document,
    Entity,
    EntityAlias,
    EntityMention,
    EntityNeighborhood,
    EntitySummary,
    ExtractorRun,
    Fetch,
    Source,
)
from backend.db.store import content_digest
from backend.web_api import delete_source


def test_delete_source_hard_deletes_source_scoped_data(store, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    get_settings.cache_clear()
    upload_path = Path(tmp_path) / "upload.txt"
    upload_path.write_text("source file", encoding="utf-8")

    source = store.upsert_source(
        kind="file",
        identifier=upload_path.name,
        display_name="Upload",
    )
    run = store.create_source_run(
        source_id=source.id,
        kind="seed",
        spec={"path": str(upload_path)},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="file://upload.txt",
        body_bytes=b"Errik Anderson founded Example.",
        http_status=200,
        content_type="text/plain",
    )
    document = store.create_document_with_chunks(
        content_hash=content_digest(b"Errik Anderson founded Example."),
        cleaned_text="Errik Anderson founded Example.",
        title="Upload",
        canonical_url="file://upload.txt",
        language="en",
        word_count=4,
        first_seen_fetch_id=fetch.id,
        chunks=[("Errik Anderson founded Example.", 4, None)],
    )

    with store.session() as session:
        chunk_id = session.execute(
            select(Chunk.id).where(Chunk.document_id == document.id)
        ).scalar_one()
        extractor_run = ExtractorRun(model="test", prompt_version="v1")
        errik = Entity(kind="person", canonical_name="Errik Anderson")
        company = Entity(kind="org", canonical_name="Example")
        session.add_all([extractor_run, errik, company])
        session.flush()
        raw = ClaimRaw(
            chunk_id=chunk_id,
            extractor_run_id=extractor_run.id,
            subject_text="Errik Anderson",
            predicate="founded",
            object_text="Example",
            object_type="org",
            raw_quote="Errik Anderson founded Example.",
        )
        session.add(raw)
        session.flush()
        claim = Claim(
            subject_entity_id=errik.id,
            predicate="founded",
            object_entity_id=company.id,
            confidence_score=0.9,
        )
        session.add(claim)
        session.flush()
        session.add_all(
            [
                ClaimEvidence(
                    claim_id=claim.id,
                    claim_raw_id=raw.id,
                    source_id=source.id,
                    weight=1.0,
                ),
                EntityMention(
                    claim_raw_id=raw.id,
                    position="subject",
                    entity_id=errik.id,
                    mention_text="Errik Anderson",
                    resolution_method="new_entity",
                    resolution_confidence=1.0,
                ),
                EntityMention(
                    claim_raw_id=raw.id,
                    position="object",
                    entity_id=company.id,
                    mention_text="Example",
                    resolution_method="new_entity",
                    resolution_confidence=1.0,
                ),
                EntityAlias(entity_id=errik.id, alias="Errik Anderson"),
                EntitySummary(
                    entity_id=errik.id,
                    display_name="Errik Anderson",
                    primary_attributes={},
                    connection_count=1,
                    source_count=1,
                    confidence_avg=0.9,
                ),
                EntityNeighborhood(
                    entity_id=errik.id,
                    neighbor_id=company.id,
                    predicates=["founded"],
                    evidence_count=1,
                    confidence=0.9,
                ),
            ]
        )
        session.commit()

    assert delete_source(store, source.id) is True
    assert upload_path.exists() is False

    counts = store.table_counts(
        [
            "sources",
            "source_runs",
            "fetches",
            "documents",
            "document_fetches",
            "chunks",
            "claims_raw",
            "entities",
            "entity_aliases",
            "entity_mentions",
            "claims",
            "claim_evidence",
            "entity_summary",
            "entity_neighborhood",
        ]
    )
    assert counts == {
        "sources": 0,
        "source_runs": 0,
        "fetches": 0,
        "documents": 0,
        "document_fetches": 0,
        "chunks": 0,
        "claims_raw": 0,
        "entities": 0,
        "entity_aliases": 0,
        "entity_mentions": 0,
        "claims": 0,
        "claim_evidence": 0,
        "entity_summary": 0,
        "entity_neighborhood": 0,
    }


def test_delete_source_repoints_shared_document_first_seen_fetch(store) -> None:
    first_source = store.upsert_source(kind="domain", identifier="first.example")
    second_source = store.upsert_source(kind="domain", identifier="second.example")
    first_run = store.create_source_run(
        source_id=first_source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    second_run = store.create_source_run(
        source_id=second_source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    first_fetch = store.add_fetch(
        source_run_id=first_run.id,
        url="https://first.example/shared",
        body_bytes=b"Shared document",
    )
    second_fetch = store.add_fetch(
        source_run_id=second_run.id,
        url="https://second.example/shared",
        body_bytes=b"Shared document",
    )
    document = store.create_document_with_chunks(
        content_hash=content_digest(b"Shared document"),
        cleaned_text="Shared document",
        title="Shared",
        canonical_url="https://example.com/shared",
        language="en",
        word_count=2,
        first_seen_fetch_id=first_fetch.id,
        chunks=[("Shared document", 2, None)],
    )
    store.link_document_fetch(document.id, second_fetch.id)

    assert delete_source(store, first_source.id) is True

    with store.session() as session:
        remaining_document = session.get(Document, document.id)
        remaining_fetch = session.get(Fetch, second_fetch.id)
        removed_source = session.get(Source, first_source.id)

    assert removed_source is None
    assert remaining_fetch is not None
    assert remaining_document is not None
    assert remaining_document.first_seen_fetch_id == second_fetch.id


def test_delete_document_removes_derived_rows_but_keeps_entities(store, admin_headers) -> None:
    source = store.upsert_source(kind="domain", identifier="doc-delete.example")
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://doc-delete.example/story",
        body_bytes=b"Errik Anderson founded Example.",
    )
    document = store.create_document_with_chunks(
        content_hash=content_digest(b"Errik Anderson founded Example."),
        cleaned_text="Errik Anderson founded Example.",
        title="Story",
        canonical_url="https://doc-delete.example/story",
        language="en",
        word_count=4,
        first_seen_fetch_id=fetch.id,
        chunks=[("Errik Anderson founded Example.", 4, None)],
    )

    with store.session() as session:
        chunk_id = session.execute(
            select(Chunk.id).where(Chunk.document_id == document.id)
        ).scalar_one()
        extractor_run = ExtractorRun(model="test", prompt_version="v1")
        errik = Entity(kind="person", canonical_name="Errik Anderson")
        company = Entity(kind="org", canonical_name="Example")
        session.add_all([extractor_run, errik, company])
        session.flush()
        raw = ClaimRaw(
            chunk_id=chunk_id,
            extractor_run_id=extractor_run.id,
            subject_text="Errik Anderson",
            predicate="founded",
            object_text="Example",
            object_type="org",
            raw_quote="Errik Anderson founded Example.",
        )
        session.add(raw)
        session.flush()
        claim = Claim(
            subject_entity_id=errik.id,
            predicate="founded",
            object_entity_id=company.id,
            confidence_score=0.9,
        )
        session.add(claim)
        session.flush()
        session.add_all(
            [
                ClaimEvidence(
                    claim_id=claim.id,
                    claim_raw_id=raw.id,
                    source_id=source.id,
                    weight=1.0,
                ),
                EntityMention(
                    claim_raw_id=raw.id,
                    position="subject",
                    entity_id=errik.id,
                    mention_text="Errik Anderson",
                    resolution_method="new_entity",
                    resolution_confidence=1.0,
                ),
            ]
        )
        session.commit()
        entity_ids = {errik.id, company.id}

    with TestClient(main_module.create_app(store)) as client:
        response = client.delete(f"/admin/documents/{document.id}", headers=admin_headers)
        missing = client.get(f"/api/document/{document.id}")

    assert response.status_code == 200
    assert response.json() == {"status": "deleted"}
    assert missing.status_code == 404
    counts = store.table_counts(
        [
            "sources",
            "source_runs",
            "fetches",
            "documents",
            "document_fetches",
            "chunks",
            "claims_raw",
            "entities",
            "entity_mentions",
            "claims",
            "claim_evidence",
        ]
    )
    assert counts == {
        "sources": 1,
        "source_runs": 1,
        "fetches": 1,
        "documents": 0,
        "document_fetches": 0,
        "chunks": 0,
        "claims_raw": 0,
        "entities": 2,
        "entity_mentions": 0,
        "claims": 0,
        "claim_evidence": 0,
    }
    with store.session() as session:
        remaining_entity_ids = set(session.execute(select(Entity.id)).scalars())
    assert remaining_entity_ids == entity_ids


def test_archive_status_hides_source_without_deleting_data(
    store,
    admin_headers,
) -> None:
    source = store.upsert_source(
        kind="domain",
        identifier="archive.example",
        display_name="Archive Example",
    )
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url="https://archive.example/story",
        body_bytes=b"Archived source data",
    )
    store.create_document_with_chunks(
        content_hash=content_digest(b"Archived source data"),
        cleaned_text="Archived source data",
        title="Archived",
        canonical_url="https://archive.example/story",
        language="en",
        word_count=3,
        first_seen_fetch_id=fetch.id,
        chunks=[("Archived source data", 3, None)],
    )

    with TestClient(main_module.create_app(store)) as client:
        archive = client.patch(
            f"/admin/sources/{source.id}",
            headers=admin_headers,
            json={"status": "archived"},
        )
        assert archive.status_code == 200
        assert archive.json()["status"] == "archived"

        main_sources = client.get("/api/sources").json()
        archived_sources = client.get("/api/sources/archived").json()

        restore = client.patch(
            f"/admin/sources/{source.id}",
            headers=admin_headers,
            json={"status": "active"},
        )
        assert restore.status_code == 200

    counts = store.table_counts(["sources", "source_runs", "fetches", "documents", "chunks"])

    assert main_sources["archived_count"] == 1
    assert main_sources["sources"] == []
    assert archived_sources["archived_count"] == 1
    assert archived_sources["sources"][0]["status"] == "archived"
    assert counts == {
        "sources": 1,
        "source_runs": 1,
        "fetches": 1,
        "documents": 1,
        "chunks": 1,
    }


def test_source_status_does_not_parse_user_notes(store, admin_headers) -> None:
    source = store.upsert_source(
        kind="domain",
        identifier="notes.example",
        notes="status:archived\nThis is user-authored text.",
    )

    with TestClient(main_module.create_app(store)) as client:
        listed = client.get("/api/sources").json()
        update = client.patch(
            f"/admin/sources/{source.id}",
            headers=admin_headers,
            json={"status": "paused", "notes": "status:active is just a note"},
        )

    assert listed["sources"][0]["status"] == "active"
    assert listed["sources"][0]["notes"].startswith("status:archived")
    assert update.status_code == 200
    assert update.json()["status"] == "paused"
    assert update.json()["notes"] == "status:active is just a note"
