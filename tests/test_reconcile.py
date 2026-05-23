from __future__ import annotations

from sqlalchemy import select

from backend.db.models import Connection, EntityConsolidated
from backend.db.store import Store
from backend.pipeline.reconcile import reconcile_graph


def test_reconcile_consolidates_entities_and_infers_project_and_classmate_edges(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'reconcile.db'}")
    store.init_db()
    errik = store.upsert_profile(name="Errik Anderson", class_year="T'07")
    daniella = store.upsert_profile(name="Daniella Reichstetter", class_year="T'07")
    errik_page = store.save_raw_page(
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        source_url="https://example.com/errik",
        page_title="Errik",
        page_text="Errik Anderson worked on Gyrobike.",
    )
    daniella_page = store.save_raw_page(
        alum_name="Daniella Reichstetter",
        entity_id=daniella.entity_id,
        source_url="https://example.com/daniella",
        page_title="Daniella",
        page_text="Daniella Reichstetter worked on Gyrobike.",
    )
    store.replace_structured_items(
        raw_page_id=errik_page.id,
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        facts=[],
        connections=[],
        projects=[
            {
                "subject_name": "Errik Anderson",
                "subject_entity_id": str(errik.entity_id),
                "project_name": "Gyrobike",
                "description": "Bike training project.",
                "confidence_score": 0.9,
                "validation_verdict": "keep",
            }
        ],
    )
    store.replace_structured_items(
        raw_page_id=daniella_page.id,
        alum_name="Daniella Reichstetter",
        entity_id=daniella.entity_id,
        facts=[],
        connections=[],
        projects=[
            {
                "subject_name": "Daniella Reichstetter",
                "subject_entity_id": str(daniella.entity_id),
                "project_name": "Gyrobike",
                "description": "Bike training project.",
                "confidence_score": 0.8,
                "validation_verdict": "keep",
            }
        ],
    )

    first = reconcile_graph(store)
    second = reconcile_graph(store)

    assert first.entities_consolidated == 2
    assert first.inferred_connections == second.inferred_connections == 2
    with store.session() as session:
        consolidated = list(session.execute(select(EntityConsolidated)).scalars())
        inferred = list(
            session.execute(
                select(Connection)
                .where(Connection.is_inferred.is_(True))
                .order_by(Connection.relationship_type.asc())
            ).scalars()
        )

    assert {row.name for row in consolidated} == {"Errik Anderson", "Daniella Reichstetter"}
    assert {row.class_year for row in consolidated} == {"T'07"}
    relationship_types = {connection.relationship_type for connection in inferred}
    assert "classmate:T'07" in relationship_types
    assert "co_worked_on:gyrobike" in relationship_types
    project_edge = next(
        connection
        for connection in inferred
        if connection.relationship_type == "co_worked_on:gyrobike"
    )
    assert project_edge.confidence_score == 0.8
    assert project_edge.derivation
    assert set(project_edge.source_ids) == {"project:1", "project:2"}


def test_reconcile_resolves_explicit_connection_entity_and_project_targets(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'explicit-reconcile.db'}")
    store.init_db()
    errik = store.upsert_profile(name="Errik Anderson", class_year="T'07")
    daniella = store.upsert_profile(name="Daniella Reichstetter", class_year="T'07")
    page = store.save_raw_page(
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        source_url="https://example.com/errik",
        page_title="Errik",
        page_text="Errik partnered with Daniella Reichstetter on Gyrobike.",
    )
    store.replace_structured_items(
        raw_page_id=page.id,
        alum_name="Errik Anderson",
        entity_id=errik.entity_id,
        facts=[],
        connections=[],
        projects=[
            {
                "subject_name": "Errik Anderson",
                "subject_entity_id": str(errik.entity_id),
                "project_name": "Gyrobike",
                "description": "Bike training project.",
                "confidence_score": 0.9,
                "validation_verdict": "keep",
            }
        ],
    )
    with store.session() as session:
        session.add(
            Connection(
                alum_name="Errik Anderson",
                entity_id=errik.entity_id,
                connected_name="Daniella Reichstetter T'07",
                source_raw_page_id=page.id,
                relationship_type="partnered with to license invention",
                text_evidence="Errik partnered with Daniella Reichstetter T'07.",
                validation_verdict="keep",
            )
        )
        session.add(
            Connection(
                alum_name="Errik Anderson",
                entity_id=errik.entity_id,
                connected_name="Gyrobike",
                source_raw_page_id=page.id,
                relationship_type="worked_on_project",
                text_evidence="Errik worked on Gyrobike.",
                validation_verdict="keep",
            )
        )
        session.commit()

    summary = reconcile_graph(store)

    assert summary.explicit_connections_resolved == 1
    assert summary.explicit_projects_resolved == 1
    with store.session() as session:
        edges = list(
            session.execute(
                select(Connection)
                .where(Connection.is_inferred.is_(False))
                .order_by(Connection.id.asc())
            ).scalars()
        )
    daniella_edge = next(edge for edge in edges if "Daniella" in edge.connected_name)
    gyrobike_edge = next(edge for edge in edges if edge.connected_name == "Gyrobike")
    assert daniella_edge.connected_entity_id == daniella.entity_id
    assert daniella_edge.relationship_type == "partnered_with"
    assert daniella_edge.derivation == "to license invention"
    assert gyrobike_edge.connected_project_id is not None
