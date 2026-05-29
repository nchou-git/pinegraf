from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import exists, select

from backend.db.models import ClaimRaw, EntityMention
from backend.db.store import Store
from backend.resolution.resolver import ENTITY_OBJECT_TYPES, resolve_mention, write_mention


async def resolve_pending(
    limit: int | None = None,
    *,
    store: Store,
    progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> list[uuid.UUID]:
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
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        subject = await resolve_mention(
            row.subject_text,
            row.subject_type,
            store=store,
            context_chunk=_context_chunk(store, row.chunk_id),
            context_chunk_id=row.chunk_id,
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
                context_chunk=_context_chunk(store, row.chunk_id),
                context_chunk_id=row.chunk_id,
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
        if progress is not None:
            await progress(index, total)
    return touched


def _context_chunk(store: Store, chunk_id: uuid.UUID) -> str:
    from backend.db.models import Chunk

    with store.session() as session:
        chunk = session.get(Chunk, chunk_id)
        return chunk.text if chunk is not None else ""
