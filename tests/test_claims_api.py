from __future__ import annotations

from datetime import timedelta

from claim_helpers import create_claim_graph

from backend.db.models import Claim
from backend.db.store import utc_now
from backend.web_api import list_claims


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

    confident = list_claims(store, min_confidence=0.8)
    assert confident["total"] == 1
    assert confident["claims"][0]["confidence"] == 0.9

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

    assert payload["claims"][0]["sources"] == [
        {
            "id": str(graph["source"].id),
            "name": "claims.example",
            "url": "https://claims.example/profile",
            "document_id": str(graph["document"].id),
            "title": "Profile",
            "fetched_at": graph["fetch"].fetched_at.isoformat(),
            "snippet": "Erik Snowberg is employed by Tuck School of Business.",
        }
    ]
