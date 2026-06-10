from __future__ import annotations

from backend.db.models import Claim, ClaimEvidence, ClaimRaw, Entity, ExtractorRun
from backend.db.store import content_digest


def create_claim_graph(
    store,
    *,
    source_identifier: str = "claims.example",
    subject_name: str = "Erik Snowberg",
    object_name: str = "Tuck School of Business",
    predicate: str = "employed_by",
    confidence: float = 0.81,
    url: str = "https://claims.example/profile",
    chunk_text: str = "Erik Snowberg is employed by Tuck School of Business. He teaches economics.",
):
    source = store.upsert_source(kind="domain", identifier=source_identifier)
    run = store.create_source_run(
        source_id=source.id,
        kind="sitemap",
        spec={},
        triggered_by="test",
        status="complete",
    )
    fetch = store.add_fetch(
        source_run_id=run.id,
        url=url,
        body_bytes=chunk_text.encode(),
        http_status=200,
    )
    document = store.create_document(
        content_hash=content_digest(f"{source_identifier}|{url}|{chunk_text}".encode()),
        cleaned_text=chunk_text,
        title="Profile",
        canonical_url=url,
        language="en",
        word_count=len(chunk_text.split()),
        first_seen_fetch_id=fetch.id,
    )
    with store.session() as session:
        subject = Entity(kind="person", canonical_name=subject_name)
        obj = Entity(kind="org", canonical_name=object_name)
        extractor_run = ExtractorRun(model="test", prompt_version="test", status="complete")
        session.add_all([subject, obj, extractor_run])
        session.flush()
        raw = ClaimRaw(
            document_id=document.id,
            extractor_run_id=extractor_run.id,
            subject_text=subject_name,
            predicate=predicate,
            object_text=object_name,
            object_type="org",
            confidence_internal=confidence,
            raw_quote=chunk_text.split(".")[0] + ".",
        )
        claim = Claim(
            subject_entity_id=subject.id,
            predicate=predicate,
            object_entity_id=obj.id,
        )
        session.add_all([raw, claim])
        session.flush()
        session.add(
            ClaimEvidence(
                claim_id=claim.id,
                claim_raw_id=raw.id,
                source_id=source.id,
            )
        )
        session.commit()
        return {
            "source": source,
            "run": run,
            "fetch": fetch,
            "document": document,
            "subject": subject,
            "object": obj,
            "claim": claim,
            "raw": raw,
        }
