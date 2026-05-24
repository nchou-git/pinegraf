from __future__ import annotations

import uuid

from sqlalchemy import exists, select

from backend.db.models import ClaimRaw, EntityMention
from backend.db.store import Store
from backend.resolution.resolver import ENTITY_OBJECT_TYPES, resolve_mention, write_mention


async def resolve_pending(
    workspace_id: str = "tuck",
    limit: int | None = None,
    *,
    store: Store,
) -> list[uuid.UUID]:
    del workspace_id
    with store.session() as session:
        query = (
            select(ClaimRaw)
            .where(~exists().where(EntityMention.claim_raw_id == ClaimRaw.id))
            .order_by(ClaimRaw.extracted_at.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        rows = list(session.execute(query).scalars())

    touched: list[uuid.UUID] = []
    for row in rows:
        subject = await resolve_mention(
            row.subject_text,
            "person",
            store=store,
            context=row.raw_quote,
        )
        if subject is not None:
            write_mention(
                store=store,
                claim_raw_id=row.id,
                position="subject",
                mention_text=row.subject_text,
                resolution=subject,
            )
            touched.append(subject.entity_id)
        if row.object_text and row.object_type in ENTITY_OBJECT_TYPES:
            object_resolution = await resolve_mention(
                row.object_text,
                row.object_type or "org",
                store=store,
                context=row.raw_quote,
            )
            if object_resolution is not None:
                write_mention(
                    store=store,
                    claim_raw_id=row.id,
                    position="object",
                    mention_text=row.object_text,
                    resolution=object_resolution,
                )
                touched.append(object_resolution.entity_id)
    return touched
