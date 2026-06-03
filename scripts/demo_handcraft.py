from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select, text

from backend.config import get_settings
from backend.db.models import Claim, Entity, EntityAlias
from backend.db.store import Store


@dataclass(frozen=True)
class DemoClaim:
    subject: str
    predicate: str
    object_name: str | None = None
    object_kind: str = "org"
    object_value: str | None = None


CLAIMS = [
    DemoClaim("Errik Anderson", "affiliated_with", "Aakha Biologics board", "org"),
    DemoClaim("Errik Anderson", "affiliated_with", "Alumni Ventures director", "org"),
    DemoClaim("Errik Anderson", "affiliated_with", "Tuck MBA Council", "org"),
    DemoClaim(
        "Errik Anderson",
        "affiliated_with",
        "Dartmouth Center for Entrepreneurship board",
        "org",
    ),
    DemoClaim("Cuong Do", "employed_by", "BioVie", "org"),
    DemoClaim("Gunnar Esiason", "affiliated_with", "Cystic Fibrosis Foundation", "org"),
    DemoClaim("Gunnar Esiason", "affiliated_with", "Vertex Pharmaceuticals", "org"),
    DemoClaim("Tillman Gerngross", "founded", "Adimab", "org"),
    DemoClaim("Gyrobike", "description", object_value="auto-balancing bicycle"),
    DemoClaim("Gyrobike", "developed_at", "Thayer School of Engineering", "org"),
    DemoClaim("Gyrobike", "affiliated_with", "Dartmouth Engineering", "org"),
    DemoClaim("Gyrobike", "renamed_to", "Jyrobike", "project"),
    DemoClaim("Errik Anderson", "codeveloped", "Gyrobike", "project"),
    DemoClaim("Daniella Reichstetter", "current_title", object_value="founder and CEO of Gyrobike"),
    DemoClaim(
        "Daniella Reichstetter",
        "current_title",
        object_value="Chair of Whaleback Mountain Board",
    ),
    DemoClaim("Daniella Reichstetter", "affiliated_with", "VCIC judge", "event"),
    DemoClaim("Sarah Ketterer", "cofounded_with", "Harry Hartford", "person"),
]

ALIASES = {
    "Gyrobike": ["Jyrobike"],
    "Cystic Fibrosis Foundation": ["CFF"],
    "Bristol-Myers Squibb": ["BMS"],
    "University of Texas Southwestern Medical Center President's Advisory Board": [
        "UT Southwestern President's Advisory Board",
        "UT Southwestern PAB",
    ],
}


def main() -> None:
    if os.getenv("PINEGRAF_DEMO_MODE", "").casefold() not in {"1", "true", "yes", "on"}:
        raise SystemExit("Set PINEGRAF_DEMO_MODE=true before running demo handcraft seed.")

    settings = get_settings()
    store = Store(settings.database_url)
    created_claims = 0
    created_entities = 0
    created_aliases = 0

    with store.session() as session:
        for canonical_name, aliases in ALIASES.items():
            entity, entity_created = _entity(session, canonical_name, "org")
            created_entities += int(entity_created)
            for alias in aliases:
                if _alias_exists(session, entity.id, alias):
                    continue
                session.add(EntityAlias(entity_id=entity.id, alias=alias, source="demo_handcraft"))
                created_aliases += 1

        for item in CLAIMS:
            subject, subject_created = _entity(session, item.subject, "person")
            created_entities += int(subject_created)
            object_entity = None
            if item.object_name:
                object_entity, object_created = _entity(session, item.object_name, item.object_kind)
                created_entities += int(object_created)
            if _claim_exists(session, subject, item.predicate, object_entity, item.object_value):
                continue
            session.execute(
                text(
                    """
                    insert into claims (
                        id,
                        subject_entity_id,
                        predicate,
                        object_entity_id,
                        object_value,
                        qualifiers,
                        stale_warning
                    )
                    values (
                        :id,
                        :subject_entity_id,
                        :predicate,
                        :object_entity_id,
                        :object_value,
                        cast(:qualifiers as jsonb),
                        false
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "subject_entity_id": subject.id,
                    "predicate": item.predicate,
                    "object_entity_id": object_entity.id if object_entity else None,
                    "object_value": item.object_value,
                    "qualifiers": '{"source": "demo_handcraft"}',
                },
            )
            created_claims += 1
        session.commit()

    print(
        "demo handcraft complete: "
        f"created_entities={created_entities} "
        f"created_aliases={created_aliases} "
        f"created_claims={created_claims}"
    )


def _entity(session, canonical_name: str, kind: str) -> tuple[Entity, bool]:
    entity = session.execute(
        select(Entity).where(Entity.canonical_name.ilike(canonical_name)).limit(1)
    ).scalar_one_or_none()
    if entity:
        return entity, False
    entity = Entity(kind=kind, canonical_name=canonical_name)
    session.add(entity)
    session.flush()
    return entity, True


def _alias_exists(session, entity_id, alias: str) -> bool:
    return bool(
        session.execute(
            select(EntityAlias.id)
            .where(EntityAlias.entity_id == entity_id)
            .where(EntityAlias.alias == alias)
            .limit(1)
        ).scalar_one_or_none()
    )


def _claim_exists(
    session,
    subject: Entity,
    predicate: str,
    object_entity: Entity | None,
    object_value: str | None,
) -> bool:
    conditions = [
        Claim.subject_entity_id == subject.id,
        Claim.predicate == predicate,
    ]
    if object_entity:
        conditions.append(Claim.object_entity_id == object_entity.id)
    else:
        conditions.append(
            and_(Claim.object_entity_id.is_(None), Claim.object_value == object_value)
        )
    return bool(
        session.execute(select(Claim.id).where(and_(*conditions)).limit(1)).scalar_one_or_none()
    )


if __name__ == "__main__":
    main()
