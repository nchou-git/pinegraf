from __future__ import annotations

from datetime import timedelta

from claim_helpers import create_claim_graph
from fastapi.testclient import TestClient

from backend import main as main_module
from backend.db.models import Claim, ClaimRaw
from backend.db.store import utc_now
from backend.web_api import list_claims, list_raw_claims


def test_claims_api_filters_and_paginates(store) -> None:
    first = create_claim_graph(store, source_identifier="first.example", confidence=0.9)
    second = create_claim_graph(
        store,
        source_identifier="second.example",
        subject_name="Jane Doe",
        object_name="Acme",
        predicate="founded",
        confidence=0.5,
        url="https://second.example/profile",
    )

    by_predicate = list_claims(store, predicate="employed_by")
    assert by_predicate["total"] == 1
    assert by_predicate["claims"][0]["id"] == str(first["claim"].id)

    by_subject = list_claims(store, subject_entity_id=second["subject"].id)
    assert by_subject["total"] == 1
    assert by_subject["claims"][0]["subject"]["name"] == "Jane Doe"

    by_source = list_claims(store, source_id=first["source"].id)
    assert by_source["total"] == 1
    assert by_source["claims"][0]["sources"][0]["url"] == "https://claims.example/profile"

    page = list_claims(store, status="all", page=1, page_size=1)
    assert page["total"] == 2
    assert page["page_size"] == 1


def test_claims_api_status_filter(store) -> None:
    graph = create_claim_graph(store)
    with store.session() as session:
        claim = session.get(Claim, graph["claim"].id)
        claim.valid_to = utc_now() - timedelta(days=1)
        session.commit()

    assert list_claims(store, status="current")["total"] == 0
    assert list_claims(store, status="superseded")["total"] == 1


def test_claim_sources_are_deduped_and_capped(store) -> None:
    graph = create_claim_graph(store)

    payload = list_claims(store, status="all")

    source = payload["claims"][0]["sources"][0]
    assert source["id"] == str(graph["source"].id)
    assert source["name"] == "claims.example"
    assert source["url"] == "https://claims.example/profile"
    assert source["document_id"] == str(graph["document"].id)
    assert source["title"] == "Profile"
    assert source["fetched_at"]
    assert source["snippet"] == (
        "Erik Snowberg is employed by Tuck School of Business. He teaches economics."
    )


def test_raw_claims_endpoint_returns_undeduped_source_cited_rows(store, admin_headers) -> None:
    graph = create_claim_graph(store)
    with store.session() as session:
        session.add(
            ClaimRaw(
                document_id=graph["document"].id,
                extractor_run_id=graph["raw"].extractor_run_id,
                subject_text=graph["raw"].subject_text,
                subject_type=graph["raw"].subject_type,
                predicate=graph["raw"].predicate,
                object_text=graph["raw"].object_text,
                object_type=graph["raw"].object_type,
                qualifiers={"duplicate": True},
                raw_quote=graph["raw"].raw_quote,
                span_start=0,
                span_end=24,
            )
        )
        session.commit()

    payload = list_raw_claims(store, page_size=10)
    assert payload["total"] == 2
    assert len(payload["claims_raw"]) == 2
    row = payload["claims_raw"][0]
    assert row["source_url"] == "https://claims.example/profile"
    assert row["document_id"] == str(graph["document"].id)
    assert row["extractor_run_id"] == str(graph["raw"].extractor_run_id)
    assert "span" in row

    with TestClient(main_module.create_app(store)) as client:
        response = client.get("/api/claims/raw-data?page_size=10", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["total"] == 2
