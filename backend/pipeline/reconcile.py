from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import (
    AlumniProfile,
    Connection,
    Entity,
    EntityAlias,
    EntityAttribute,
    EntityConsolidated,
    Fact,
    Project,
)
from backend.db.store import Store
from backend.pipeline.position_dates import date_ranges_overlap, parse_position_date
from backend.pipeline.relationship_types import normalize_relationship_type
from backend.resolution.entity_resolver import _normalize_match_value


@dataclass(frozen=True)
class ReconcileSummary:
    entities_consolidated: int
    inferred_connections: int
    explicit_connections_resolved: int = 0
    explicit_projects_resolved: int = 0


def reconcile_graph(store: Store) -> ReconcileSummary:
    with store.session() as session:
        session.query(Connection).filter(Connection.is_inferred.is_(True)).delete()
        session.query(EntityConsolidated).delete()

        consolidated = consolidate_entities(session)
        for row in consolidated:
            session.add(row)
        session.flush()

        explicit_entities_resolved, explicit_projects_resolved = resolve_explicit_connections(
            session
        )
        session.flush()

        inferred = [
            *infer_project_connections(session),
            *infer_company_connections(session),
            *infer_classmate_connections(session),
        ]
        for connection in _dedupe_inferred(inferred):
            session.add(connection)
        session.commit()
        return ReconcileSummary(
            entities_consolidated=len(consolidated),
            inferred_connections=len(_dedupe_inferred(inferred)),
            explicit_connections_resolved=explicit_entities_resolved,
            explicit_projects_resolved=explicit_projects_resolved,
        )


def resolve_explicit_connections(session: Session) -> tuple[int, int]:
    entity_index = _entity_name_index(session)
    project_index = _project_name_index(session)
    rows = list(
        session.execute(
            select(Connection).where(
                Connection.is_inferred.is_(False),
                Connection.validation_verdict != "drop",
            )
        ).scalars()
    )
    entity_resolved = 0
    project_resolved = 0
    for connection in rows:
        normalized = normalize_relationship_type(connection.relationship_type)
        if connection.relationship_type != normalized.relationship_type:
            connection.derivation = _merge_derivation(
                connection.derivation,
                normalized.derivation,
            )
            connection.relationship_type = normalized.relationship_type
        if connection.connected_entity_id is None:
            entity_id = _resolve_existing_entity_id(entity_index, connection.connected_name)
            if entity_id is not None:
                connection.connected_entity_id = entity_id
                entity_resolved += 1
                continue
        if connection.connected_project_id is None:
            project_id = _resolve_existing_project_id(project_index, connection)
            if project_id is not None:
                connection.connected_project_id = project_id
                project_resolved += 1
    return entity_resolved, project_resolved


def consolidate_entities(session: Session) -> list[EntityConsolidated]:
    entities = list(session.execute(select(Entity).order_by(Entity.created_at.asc())).scalars())
    output: list[EntityConsolidated] = []
    for entity in entities:
        attrs = list(
            session.execute(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == entity.id,
                    EntityAttribute.validation_verdict != "drop",
                )
            ).scalars()
        )
        fields = {
            "current_employer": _best_attribute(attrs, ["current_employer", "current_company"]),
            "current_title": _best_attribute(attrs, ["current_title"]),
            "class_year": _best_attribute(attrs, ["class_year"]),
            "location": _best_attribute(attrs, ["current_location"]),
        }
        output.append(
            EntityConsolidated(
                entity_id=entity.id,
                name=entity.canonical_name,
                current_employer=fields["current_employer"][0],
                current_title=fields["current_title"][0],
                class_year=fields["class_year"][0],
                location=fields["location"][0],
                source_ids={
                    field_name: source_ids
                    for field_name, (_value, source_ids) in fields.items()
                    if source_ids
                },
                updated_at=datetime.now(UTC),
            )
        )
    return output


def infer_project_connections(session: Session) -> list[Connection]:
    projects = list(
        session.execute(
            select(Project).where(
                Project.validation_verdict != "drop", Project.entity_id.is_not(None)
            )
        ).scalars()
    )
    by_project: dict[str, list[Project]] = {}
    for project in projects:
        by_project.setdefault(_norm(project.project_name), []).append(project)

    connections: list[Connection] = []
    for project_key, project_rows in by_project.items():
        if len(project_rows) < 2:
            continue
        project_name = project_rows[0].project_name
        for left, right in itertools.combinations(project_rows, 2):
            if left.entity_id == right.entity_id:
                continue
            confidence = min(left.confidence_score or 0.5, right.confidence_score or 0.5)
            source_ids = [f"project:{left.id}", f"project:{right.id}"]
            connections.append(
                _inferred_connection(
                    session,
                    left_entity_id=left.entity_id,
                    right_entity_id=right.entity_id,
                    relationship_type=f"co_worked_on:{_edge_token(project_key)}",
                    context=f"Shared project: {project_name}",
                    confidence=confidence,
                    source_raw_page_id=left.source_raw_page_id or right.source_raw_page_id,
                    source_ids=source_ids,
                    derivation=(
                        "co_worked_on inferred because both entities have validated project "
                        f"rows for {project_name}"
                    ),
                )
            )
    return connections


def infer_company_connections(session: Session) -> list[Connection]:
    position_rows = list(
        session.execute(
            select(Fact).where(
                Fact.category == "position",
                Fact.validation_verdict != "drop",
                Fact.entity_id.is_not(None),
            )
        ).scalars()
    )
    positions: list[dict[str, object]] = []
    for fact in position_rows:
        try:
            payload = json.loads(fact.content)
        except json.JSONDecodeError:
            continue
        company = str(payload.get("company", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not company or not title:
            continue
        positions.append(
            {
                "fact": fact,
                "company": company,
                "start_date": str(payload.get("start_date") or "").strip() or None,
                "end_date": str(payload.get("end_date") or "").strip() or None,
            }
        )

    connections: list[Connection] = []
    for left, right in itertools.combinations(positions, 2):
        left_fact = left["fact"]
        right_fact = right["fact"]
        if not isinstance(left_fact, Fact) or not isinstance(right_fact, Fact):
            continue
        if left_fact.entity_id == right_fact.entity_id:
            continue
        if _norm(left["company"]) != _norm(right["company"]):
            continue
        if not _employment_windows_overlap(left, right):
            continue
        company = str(left["company"])
        connections.append(
            _inferred_connection(
                session,
                left_entity_id=left_fact.entity_id,
                right_entity_id=right_fact.entity_id,
                relationship_type=f"co_worked_at:{_edge_token(company)}",
                context=f"Overlapping employment at {company}",
                confidence=min(
                    left_fact.confidence_score or 0.5, right_fact.confidence_score or 0.5
                ),
                source_raw_page_id=left_fact.source_raw_page_id or right_fact.source_raw_page_id,
                source_ids=[f"fact:{left_fact.id}", f"fact:{right_fact.id}"],
                derivation=(
                    "co_worked_at inferred because both entities have overlapping position "
                    f"windows at {company}"
                ),
            )
        )
    return connections


def infer_classmate_connections(session: Session) -> list[Connection]:
    rows = list(session.execute(select(EntityConsolidated)).scalars())
    by_year: dict[str, list[EntityConsolidated]] = {}
    for row in rows:
        if row.class_year.startswith("T'"):
            by_year.setdefault(row.class_year, []).append(row)

    connections: list[Connection] = []
    for class_year, classmates in by_year.items():
        for left, right in itertools.combinations(classmates, 2):
            left_sources = _field_sources(left, "class_year")
            right_sources = _field_sources(right, "class_year")
            connections.append(
                _inferred_connection(
                    session,
                    left_entity_id=left.entity_id,
                    right_entity_id=right.entity_id,
                    relationship_type=f"classmate:{class_year}",
                    context=f"Shared Tuck class year {class_year}",
                    confidence=0.8,
                    source_raw_page_id=None,
                    source_ids=[*left_sources, *right_sources],
                    derivation=(
                        "classmate inferred because both consolidated entities share "
                        f"Tuck class year {class_year}"
                    ),
                )
            )
    return connections


def _inferred_connection(
    session: Session,
    *,
    left_entity_id,
    right_entity_id,
    relationship_type: str,
    context: str,
    confidence: float,
    source_raw_page_id: int | None,
    source_ids: list[str],
    derivation: str,
) -> Connection:
    left = session.get(EntityConsolidated, left_entity_id)
    right = session.get(EntityConsolidated, right_entity_id)
    left_name = left.name if left is not None else str(left_entity_id)
    right_name = right.name if right is not None else str(right_entity_id)
    return Connection(
        alum_name=left_name,
        entity_id=left_entity_id,
        connected_entity_id=right_entity_id,
        connected_name=right_name,
        source_raw_page_id=source_raw_page_id,
        context=context,
        relationship_type=relationship_type[:64],
        confidence_score=max(0.0, min(1.0, confidence)),
        text_evidence="",
        is_inferred=True,
        derivation=derivation,
        source_ids=source_ids,
        validation_verdict="keep",
    )


def _best_attribute(
    attrs: list[EntityAttribute],
    names: list[str],
) -> tuple[str, list[str]]:
    candidates = [attr for attr in attrs if attr.attribute_name in names]
    if not candidates:
        return "", []
    grouped: dict[str, list[EntityAttribute]] = {}
    for attr in candidates:
        grouped.setdefault(_norm(attr.attribute_value), []).append(attr)
    best_group = max(grouped.values(), key=_attribute_group_score)
    best = best_group[0]
    return best.attribute_value, [f"entity_attribute:{attr.id}" for attr in best_group]


def _attribute_group_score(attrs: list[EntityAttribute]) -> tuple[int, int, int, int]:
    newest = max((_date_score(attr.as_of_date) for attr in attrs), default=0)
    source_priority = max(_source_priority(attr.source) for attr in attrs)
    agreement = len(attrs)
    confidence = max({"high": 3, "medium": 2, "low": 1}.get(attr.confidence, 0) for attr in attrs)
    return newest, source_priority, agreement, confidence


def _source_priority(source: str) -> int:
    if source.startswith(("alumni_xlsx_v2", "wikidata")):
        return 3
    if source:
        return 2
    return 1


def _date_score(value: date | None) -> int:
    if value is None:
        return 0
    return value.toordinal()


def _employment_windows_overlap(left: dict[str, object], right: dict[str, object]) -> bool:
    return date_ranges_overlap(
        start_a=parse_position_date(left.get("start_date"), is_end_date=False),
        end_a=parse_position_date(left.get("end_date"), is_end_date=True),
        start_b=parse_position_date(right.get("start_date"), is_end_date=False),
        end_b=parse_position_date(right.get("end_date"), is_end_date=True),
    )


def _field_sources(row: EntityConsolidated, field_name: str) -> list[str]:
    values = row.source_ids.get(field_name, [])
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]


def _entity_name_index(session: Session) -> dict[str, set]:
    index: dict[str, set] = {}
    for entity_id, canonical_name in session.execute(select(Entity.id, Entity.canonical_name)):
        _add_index_value(index, canonical_name, entity_id)
    for entity_id, alias in session.execute(select(EntityAlias.entity_id, EntityAlias.alias)):
        _add_index_value(index, alias, entity_id)
    for entity_id, name in session.execute(
        select(AlumniProfile.entity_id, AlumniProfile.name).where(
            AlumniProfile.entity_id.is_not(None)
        )
    ):
        _add_index_value(index, name, entity_id)
    return index


def _project_name_index(session: Session) -> dict[str, list[Project]]:
    index: dict[str, list[Project]] = {}
    projects = list(
        session.execute(select(Project).where(Project.validation_verdict != "drop")).scalars()
    )
    for project in projects:
        index.setdefault(_normalize_project_name(project.project_name), []).append(project)
    return index


def _add_index_value(index: dict[str, set], value: str, entity_id) -> None:
    key = _normalize_match_value(value)
    if key:
        index.setdefault(key, set()).add(entity_id)


def _resolve_existing_entity_id(index: dict[str, set], value: str):
    entity_ids = index.get(_normalize_match_value(value), set())
    if len(entity_ids) != 1:
        return None
    return next(iter(entity_ids))


def _resolve_existing_project_id(
    index: dict[str, list[Project]],
    connection: Connection,
) -> int | None:
    projects = index.get(_normalize_project_name(connection.connected_name), [])
    if not projects:
        return None
    same_source = [
        project
        for project in projects
        if project.source_raw_page_id == connection.source_raw_page_id
    ]
    if len(same_source) == 1:
        return same_source[0].id
    if connection.relationship_type == "worked_on_project" and len(projects) == 1:
        return projects[0].id
    if len(projects) == 1 and _looks_project_relationship(connection.relationship_type):
        return projects[0].id
    return None


def _looks_project_relationship(relationship_type: str) -> bool:
    return relationship_type in {"worked_on_project", "co_worked_on", "founded", "related_to"}


def _normalize_project_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _merge_derivation(existing: str, incoming: str) -> str:
    existing = existing.strip()
    incoming = incoming.strip()
    if existing and incoming and incoming not in existing:
        return f"{existing}; {incoming}"
    return existing or incoming


def _dedupe_inferred(connections: list[Connection]) -> list[Connection]:
    output: list[Connection] = []
    seen: set[tuple[object, object, str]] = set()
    for connection in connections:
        left = str(connection.entity_id)
        right = str(connection.connected_entity_id)
        key = tuple(sorted([left, right])) + (connection.relationship_type,)
        if key in seen:
            continue
        seen.add(key)
        output.append(connection)
    return output


def _edge_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9']+", "_", value.lower()).strip("_")
    return token[:48] or "unknown"


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())
