from __future__ import annotations

from claim_helpers import create_claim_graph
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import main as main_module
from backend.db.models import (
    AuditLog,
    Claim,
    Entity,
    EntityAlias,
    EntityDisambiguationCandidate,
    EntityMention,
)


def _create_review_candidate(store):
    graph = create_claim_graph(
        store,
        subject_name="Alex Doe",
        object_name="Widget Labs",
        predicate="employed_by",
        chunk_text="Alex Doe works at Widget Labs.",
    )
    with store.session() as session:
        source_entity = session.get(Entity, graph["subject"].id)
        target_entity = Entity(kind="person", canonical_name="Alex Dough")
        session.add(target_entity)
        session.add(
            EntityAlias(
                entity_id=source_entity.id,
                alias="A. Doe",
                confidence=0.8,
                source="test",
            )
        )
        session.flush()
        mention = EntityMention(
            claim_raw_id=graph["raw"].id,
            position="subject",
            entity_id=source_entity.id,
            mention_text="Alex Doe",
            resolution_method="llm",
            resolution_confidence=0.74,
        )
        session.add(mention)
        session.flush()
        candidate = EntityDisambiguationCandidate(
            mention_id=mention.id,
            candidate_entity_id=target_entity.id,
            llm_decision="near_miss_review",
            llm_reasoning="Names are similar, but qualifiers need review.",
            name_similarity_score=0.82,
        )
        session.add(candidate)
        session.commit()
        return {
            "candidate_id": candidate.id,
            "mention_id": mention.id,
            "source_entity_id": source_entity.id,
            "target_entity_id": target_entity.id,
            "claim_id": graph["claim"].id,
        }


def test_identity_review_list_pending_candidates(store, admin_headers) -> None:
    ids = _create_review_candidate(store)

    with TestClient(main_module.create_app(store)) as client:
        unauthorized = client.get("/admin/identity-review")
        assert unauthorized.status_code == 401

        response = client.get("/admin/identity-review", headers=admin_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    row = data["results"][0]
    assert row["id"] == str(ids["candidate_id"])
    assert row["source_entity"]["canonical_name"] == "Alex Doe"
    assert row["candidate_entity"]["canonical_name"] == "Alex Dough"
    assert row["mention"]["text"] == "Alex Doe"


def test_identity_review_confirm_records_review_decision(store, admin_headers) -> None:
    ids = _create_review_candidate(store)

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/identity-review/{ids['candidate_id']}",
            headers=admin_headers,
            json={"decision": "confirm", "reviewer": "reviewer@example.com"},
        )

    assert response.status_code == 200
    with store.session() as session:
        row = session.get(EntityDisambiguationCandidate, ids["candidate_id"])
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "identity_review.confirm")
        ).scalar_one()
    assert row.review_decision == "confirm"
    assert row.reviewed_by == "reviewer@example.com"
    assert audit.payload["decision"] == "confirm"


def test_identity_review_split_records_review_decision(store, admin_headers) -> None:
    ids = _create_review_candidate(store)

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/identity-review/{ids['candidate_id']}",
            headers=admin_headers,
            json={"decision": "split", "reviewer": "reviewer@example.com"},
        )

    assert response.status_code == 200
    with store.session() as session:
        row = session.get(EntityDisambiguationCandidate, ids["candidate_id"])
    assert row.review_decision == "split"
    assert row.reviewed_by == "reviewer@example.com"


def test_identity_review_merge_supersedes_and_repoints_claims(store, admin_headers) -> None:
    ids = _create_review_candidate(store)

    with TestClient(main_module.create_app(store)) as client:
        response = client.post(
            f"/admin/identity-review/{ids['candidate_id']}",
            headers=admin_headers,
            json={"decision": "merge", "reviewer": "reviewer@example.com"},
        )

    assert response.status_code == 200
    with store.session() as session:
        source = session.get(Entity, ids["source_entity_id"])
        claim = session.get(Claim, ids["claim_id"])
        mention = session.get(EntityMention, ids["mention_id"])
        aliases = set(
            session.execute(
                select(EntityAlias.alias).where(EntityAlias.entity_id == ids["target_entity_id"])
            ).scalars()
        )
        audit = session.execute(
            select(AuditLog).where(AuditLog.action == "identity_review.merge")
        ).scalar_one()
        row = session.get(EntityDisambiguationCandidate, ids["candidate_id"])

    assert source.superseded_by_entity_id == ids["target_entity_id"]
    assert claim.subject_entity_id == ids["target_entity_id"]
    assert mention.entity_id == ids["target_entity_id"]
    assert {"Alex Doe", "A. Doe"}.issubset(aliases)
    assert row.review_decision == "merge"
    assert audit.payload["source_entity_id"] == str(ids["source_entity_id"])
    assert audit.payload["target_entity_id"] == str(ids["target_entity_id"])
