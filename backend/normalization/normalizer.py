from __future__ import annotations

import uuid
from datetime import datetime

from backend.db.store import Store, content_digest
from backend.normalization.chunker import chunk_text
from backend.normalization.cleaner import clean_html, clean_pdl_person, detect_language
from backend.normalization.embedder import embed_chunks

PDL_PERSON_CONTENT_TYPE = "application/pdl-person+json"


async def normalize_fetch(
    fetch_id: uuid.UUID | str,
    *,
    store: Store,
    valid_from: datetime | None = None,
) -> uuid.UUID:
    fetch_uuid = uuid.UUID(str(fetch_id))
    fetch = store.get_fetch(fetch_uuid)
    if fetch is None:
        raise ValueError(f"fetch not found: {fetch_uuid}")
    if fetch.body_bytes is None:
        raise ValueError(f"fetch has no body bytes: {fetch_uuid}")

    digest = fetch.content_hash or content_digest(fetch.body_bytes)
    if fetch.content_hash is None:
        store.update_fetch_hash(fetch_uuid, digest)

    existing = store.get_document_by_hash(digest)
    if existing is not None:
        store.link_document_fetch(existing.id, fetch_uuid)
        return existing.id

    if fetch.content_type == PDL_PERSON_CONTENT_TYPE:
        cleaned_text, title = clean_pdl_person(fetch.body_bytes)
    else:
        cleaned_text, title = clean_html(fetch.body_bytes)
    chunks = chunk_text(cleaned_text)
    embeddings = await embed_chunks([chunk.text for chunk in chunks])
    chunk_rows = [
        (chunk.text, chunk.token_count, embeddings[index] if index < len(embeddings) else None)
        for index, chunk in enumerate(chunks)
    ]
    document = store.create_document_with_chunks(
        content_hash=digest,
        cleaned_text=cleaned_text,
        title=title,
        canonical_url=fetch.url,
        language=detect_language(cleaned_text),
        word_count=len(cleaned_text.split()),
        first_seen_fetch_id=fetch_uuid,
        chunks=chunk_rows,
        valid_from=valid_from,
    )
    store.link_document_fetch(document.id, fetch_uuid)
    return document.id
