from __future__ import annotations

from claim_helpers import create_claim_graph
from fastapi.testclient import TestClient

from backend import main as main_module
from backend.db.models import ClaimRaw


def test_raw_data_endpoints_require_admin(store) -> None:
    graph = create_claim_graph(
        store,
        subject_name="Alex Doe",
        object_name="Widget Labs",
        url="https://raw.example/profile",
    )

    with TestClient(main_module.create_app(store)) as client:
        assert client.get(f"/admin/raw/documents/{graph['document'].id}").status_code == 401
        assert client.get(f"/admin/raw/chunks/{graph['raw'].chunk_id}").status_code == 401
        assert client.get("/admin/raw/claim-raw").status_code == 401


def test_raw_document_endpoint_returns_full_cleaned_text_and_chunk_index(
    store,
    admin_headers,
) -> None:
    text = "Alex Doe founded Widget Labs. The venture builds scheduling tools."
    graph = create_claim_graph(
        store,
        subject_name="Alex Doe",
        object_name="Widget Labs",
        predicate="founded",
        url="https://raw.example/profile",
        chunk_text=text,
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.get(
            f"/admin/raw/documents/{graph['document'].id}",
            headers=admin_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["canonical_url"] == "https://raw.example/profile"
    assert payload["title"] == "Profile"
    assert payload["language"] == "en"
    assert payload["word_count"] == len(text.split())
    assert payload["cleaned_text"] == text
    assert payload["chunk_count"] == 1
    assert payload["chunks"] == [
        {
            "chunk_id": str(graph["raw"].chunk_id),
            "ordinal": 0,
            "char_count": len(text),
        }
    ]


def test_raw_chunk_endpoint_returns_chunk_context_and_raw_claims(store, admin_headers) -> None:
    graph = create_claim_graph(
        store,
        subject_name="Alex Doe",
        object_name="Widget Labs",
        predicate="worked_on_project",
        url="https://raw.example/project",
        chunk_text="Alex Doe worked on Widget Labs during the fellowship.",
    )

    with TestClient(main_module.create_app(store)) as client:
        response = client.get(f"/admin/raw/chunks/{graph['raw'].chunk_id}", headers=admin_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["chunk_id"] == str(graph["raw"].chunk_id)
    assert payload["document_id"] == str(graph["document"].id)
    assert payload["document"]["canonical_url"] == "https://raw.example/project"
    assert payload["claim_raw"] == [
        {
            "id": str(graph["raw"].id),
            "subject_text": "Alex Doe",
            "predicate": "worked_on_project",
            "object_text": "Widget Labs",
            "raw_quote": "Alex Doe worked on Widget Labs during the fellowship.",
        }
    ]


def test_raw_claims_endpoint_filters_and_reports_promotion_status(
    store,
    admin_headers,
) -> None:
    graph = create_claim_graph(
        store,
        subject_name="Alex Doe",
        object_name="Widget Labs",
        predicate="founded",
        url="https://raw.example/profile",
        chunk_text="Alex Doe founded Widget Labs. Jordan Lee advised another project.",
    )
    with store.session() as session:
        session.add(
            ClaimRaw(
                chunk_id=graph["raw"].chunk_id,
                extractor_run_id=graph["raw"].extractor_run_id,
                subject_text="Jordan Lee",
                predicate="advised",
                object_text="Project Orion",
                object_type="project",
                raw_quote="Jordan Lee advised another project.",
            )
        )
        session.commit()

    with TestClient(main_module.create_app(store)) as client:
        promoted = client.get(
            "/admin/raw/claim-raw?q=Alex&predicate=founded",
            headers=admin_headers,
        )
        filtered = client.get("/admin/raw/claim-raw?q=Jordan", headers=admin_headers)

    assert promoted.status_code == 200
    promoted_payload = promoted.json()
    assert promoted_payload["total"] == 1
    assert promoted_payload["claim_raw"][0]["subject_text"] == "Alex Doe"
    assert promoted_payload["claim_raw"][0]["canonical_url"] == "https://raw.example/profile"
    assert promoted_payload["claim_raw"][0]["promoted_to_claim"] is True
    assert promoted_payload["claim_raw"][0]["promotion_status"] == "promoted"

    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["claim_raw"][0]["subject_text"] == "Jordan Lee"
    assert filtered_payload["claim_raw"][0]["promoted_to_claim"] is False
    assert filtered_payload["claim_raw"][0]["promotion_status"] == "filtered_out"
