from __future__ import annotations

import argparse
import uuid

from sqlalchemy import func, or_, select

from backend.db.models import AuditLog, Claim, Entity, EntityMention
from backend.db.store import Store
from backend.extraction.extractor import is_structurally_valid_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive invalid active person entities that have no graph attachments."
    )
    parser.add_argument("--apply", action="store_true", help="Archive eligible entities.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only. This is the default.")
    args = parser.parse_args()
    apply_changes = bool(args.apply and not args.dry_run)

    store = Store()
    archived = 0
    flagged = 0
    with store.session() as session:
        entities = list(
            session.execute(
                select(Entity)
                .where(Entity.kind == "person")
                .where(Entity.status == "active")
                .order_by(Entity.canonical_name.asc())
            ).scalars()
        )
        for entity in entities:
            if is_structurally_valid_name(entity.canonical_name, "person"):
                continue
            claim_count = _claim_count(session, entity.id)
            mention_count = _mention_count(session, entity.id)
            if claim_count == 0 and mention_count == 0:
                archived += 1
                print(f"archive {entity.id} {entity.canonical_name!r}")
                if apply_changes:
                    entity.status = "archived"
                    session.add(
                        AuditLog(
                            action="entity.auto_archived",
                            target_table="entities",
                            target_id=str(entity.id),
                            actor="cleanup_invalid_person_entities",
                            request_ip=None,
                            payload={"canonical_name": entity.canonical_name},
                        )
                    )
            else:
                flagged += 1
                print(
                    "flagged "
                    f"{entity.id} {entity.canonical_name!r} "
                    f"claims={claim_count} mentions={mention_count}"
                )
        if apply_changes:
            session.commit()
        else:
            session.rollback()
    mode = "applied" if apply_changes else "dry-run"
    print(f"summary mode={mode} archived={archived} flagged={flagged}")
    print("Run with: python3 scripts/cleanup_invalid_person_entities.py --dry-run")
    print("Apply with: python3 scripts/cleanup_invalid_person_entities.py --apply")


def _claim_count(session, entity_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(Claim)
            .where(
                or_(
                    Claim.subject_entity_id == entity_id,
                    Claim.object_entity_id == entity_id,
                )
            )
        ).scalar_one()
        or 0
    )


def _mention_count(session, entity_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(EntityMention)
            .where(EntityMention.entity_id == entity_id)
        ).scalar_one()
        or 0
    )


if __name__ == "__main__":
    main()
