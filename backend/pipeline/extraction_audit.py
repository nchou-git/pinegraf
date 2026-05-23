from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from backend.db.models import RawPage
from backend.db.store import Store
from backend.pipeline.parser import (
    ExtractionClient,
    MockExtractionClient,
    OpenAIExtractionClient,
    PageExtraction,
    chunk_page,
)
from backend.pricing import estimate_llm_dollars

CLASS_YEAR_RE = re.compile(r"T'\d\d")
AuditBucket = str


@dataclass(frozen=True)
class AuditSample:
    raw_page: RawPage
    bucket: AuditBucket


def run_extraction_audit(
    store: Store,
    *,
    sample_size: int = 30,
    use_mock_extract: bool = True,
    openai_api_key: str = "",
    max_estimated_dollars: float = 5.0,
) -> dict[str, object]:
    sample = sample_raw_pages(store.list_raw_pages(), sample_size=sample_size)
    if not use_mock_extract:
        estimated_dollars = estimate_audit_dollars([item.raw_page for item in sample])
        if estimated_dollars > max_estimated_dollars:
            raise RuntimeError(
                "Audit estimate exceeds max cost: "
                f"${estimated_dollars:.2f} > ${max_estimated_dollars:.2f}"
            )

    thrifty_results = run_audit_mode(
        sample,
        mode="cascade",
        store=store,
        use_mock_extract=use_mock_extract,
        openai_api_key=openai_api_key,
    )
    frontier_results = run_audit_mode(
        sample,
        mode="frontier_only",
        store=store,
        use_mock_extract=use_mock_extract,
        openai_api_key=openai_api_key,
    )
    diff_summary = diff_audit_results(thrifty_results, frontier_results)
    audit_run = store.create_audit_run(
        sample_size=len(sample),
        thrifty_results=thrifty_results,
        frontier_results=frontier_results,
        diff_summary=diff_summary,
    )
    return {
        "id": audit_run.id,
        "run_at": audit_run.run_at.isoformat(),
        "sample_size": audit_run.sample_size,
        "diff_summary": diff_summary,
    }


def sample_raw_pages(raw_pages: Iterable[RawPage], *, sample_size: int) -> list[AuditSample]:
    buckets: dict[AuditBucket, list[RawPage]] = {"rich": [], "medium": [], "sparse": []}
    for raw_page in raw_pages:
        buckets[bucket_for_page(raw_page)].append(raw_page)
    for pages in buckets.values():
        pages.sort(key=lambda page: page.id)

    target_per_bucket = max(1, sample_size // 3) if sample_size else 0
    sample: list[AuditSample] = []
    for bucket in ("rich", "medium", "sparse"):
        for page in buckets[bucket][:target_per_bucket]:
            sample.append(AuditSample(raw_page=page, bucket=bucket))

    if len(sample) < sample_size:
        sampled_ids = {item.raw_page.id for item in sample}
        leftovers = [
            AuditSample(raw_page=page, bucket=bucket)
            for bucket, pages in buckets.items()
            for page in pages
            if page.id not in sampled_ids
        ]
        leftovers.sort(key=lambda item: item.raw_page.id)
        sample.extend(leftovers[: sample_size - len(sample)])
    return sample[:sample_size]


def bucket_for_page(raw_page: RawPage) -> AuditBucket:
    text = raw_page.page_text or ""
    class_year_mentions = len(CLASS_YEAR_RE.findall(text))
    length = len(text)
    if length > 5000 and class_year_mentions > 1:
        return "rich"
    if 1000 <= length <= 5000 and class_year_mentions >= 1:
        return "medium"
    return "sparse"


def run_audit_mode(
    sample: list[AuditSample],
    *,
    mode: str,
    store: Store,
    use_mock_extract: bool,
    openai_api_key: str,
) -> dict[str, object]:
    with temporary_env("EXTRACTION_TIER_MODE", mode):
        extractor: ExtractionClient
        if use_mock_extract:
            extractor = MockExtractionClient()
        else:
            if not openai_api_key:
                raise RuntimeError("OPENAI_API_KEY is required for non-mock audit extraction")
            extractor = OpenAIExtractionClient(api_key=openai_api_key, store=store)
        try:
            pages = [audit_page_result(item.raw_page, item.bucket, extractor) for item in sample]
        finally:
            asyncio.run(extractor.aclose())
    return {"mode": mode, "pages": pages}


def audit_page_result(
    raw_page: RawPage,
    bucket: AuditBucket,
    extractor: ExtractionClient,
) -> dict[str, object]:
    extraction = extractor.extract_page(raw_page, chunk_page(raw_page.page_text))
    entities = sorted(entity_set(extraction))
    relationships = [
        {
            "subject_name": connection.subject_name,
            "connected_name": connection.connected_name,
            "relationship_type": connection.relationship_type,
        }
        for connection in extraction.connections
    ]
    return {
        "raw_page_id": raw_page.id,
        "source_url": raw_page.source_url,
        "bucket": bucket,
        "entities": entities,
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "relationships": relationships,
        "project_count": len(extraction.projects),
        "projects": [project.project_name for project in extraction.projects],
    }


def entity_set(extraction: PageExtraction) -> set[str]:
    entities = set()
    entities.update(
        claim.subject_name.strip() for claim in extraction.claims if claim.subject_name.strip()
    )
    entities.update(
        claim.object_name.strip() for claim in extraction.claims if claim.object_name.strip()
    )
    entities.update(
        fact.content.strip()
        for fact in extraction.facts
        if fact.category in {"person", "organization"} and fact.content.strip()
    )
    entities.update(
        connection.connected_name.strip()
        for connection in extraction.connections
        if connection.connected_name.strip()
    )
    entities.update(
        project.project_name.strip()
        for project in extraction.projects
        if project.project_name.strip()
    )
    return entities


def diff_audit_results(
    thrifty_results: dict[str, object],
    frontier_results: dict[str, object],
) -> dict[str, object]:
    thrifty_pages = _pages_by_id(thrifty_results)
    frontier_pages = _pages_by_id(frontier_results)
    all_ids = sorted(set(thrifty_pages) | set(frontier_pages))
    per_page: list[dict[str, object]] = []
    thrifty_entities: set[str] = set()
    frontier_entities: set[str] = set()
    bucket_jaccards: dict[str, list[float]] = {}
    for raw_page_id in all_ids:
        thrifty_page = thrifty_pages.get(raw_page_id, {})
        frontier_page = frontier_pages.get(raw_page_id, {})
        left = set(thrifty_page.get("entities", []))
        right = set(frontier_page.get("entities", []))
        thrifty_entities.update(left)
        frontier_entities.update(right)
        bucket = str(thrifty_page.get("bucket") or frontier_page.get("bucket") or "unknown")
        jaccard = _jaccard(left, right)
        bucket_jaccards.setdefault(bucket, []).append(jaccard)
        per_page.append(
            {
                "raw_page_id": raw_page_id,
                "page": thrifty_page.get("source_url") or frontier_page.get("source_url") or "",
                "bucket": bucket,
                "thrifty_count": len(left),
                "frontier_count": len(right),
                "thrifty_relationships": int(thrifty_page.get("relationship_count", 0) or 0),
                "frontier_relationships": int(frontier_page.get("relationship_count", 0) or 0),
                "jaccard": jaccard,
            }
        )
    buckets = {
        bucket: {
            "pages": len(values),
            "avg_jaccard": sum(values) / len(values) if values else 0.0,
        }
        for bucket, values in sorted(bucket_jaccards.items())
    }
    return {
        "per_page": per_page,
        "buckets": buckets,
        "global_jaccard": _jaccard(thrifty_entities, frontier_entities),
        "thrifty_entity_count": len(thrifty_entities),
        "frontier_entity_count": len(frontier_entities),
    }


def estimate_audit_dollars(raw_pages: Iterable[RawPage]) -> float:
    total_tokens = sum(max(1, len(page.page_text or "") // 4) for page in raw_pages)
    mini_cost = estimate_llm_dollars(
        "gpt-5.4-mini",
        prompt_tokens=total_tokens,
        completion_tokens=max(100, total_tokens // 10),
    )
    cascade_frontier_cost = estimate_llm_dollars(
        "gpt-5.4",
        prompt_tokens=max(1, total_tokens // 4),
        completion_tokens=max(100, total_tokens // 20),
    )
    frontier_cost = estimate_llm_dollars(
        "gpt-5.4",
        prompt_tokens=total_tokens,
        completion_tokens=max(100, total_tokens // 10),
    )
    return mini_cost + cascade_frontier_cost + frontier_cost


def _pages_by_id(results: dict[str, object]) -> dict[int, dict[str, object]]:
    pages = results.get("pages", [])
    if not isinstance(pages, list):
        return {}
    output: dict[int, dict[str, object]] = {}
    for page in pages:
        if not isinstance(page, dict):
            continue
        raw_page_id = page.get("raw_page_id")
        if isinstance(raw_page_id, int):
            output[raw_page_id] = page
    return output


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


@contextmanager
def temporary_env(name: str, value: str) -> Iterator[None]:
    previous = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
