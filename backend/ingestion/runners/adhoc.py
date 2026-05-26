from __future__ import annotations

import uuid

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion.auto_pipeline import enqueue_pipeline_after_crawl
from backend.ingestion.fetcher import fetch_url
from backend.progress import progress_stats


async def run_adhoc(
    source_run_id: uuid.UUID | str,
    urls: list[str],
    *,
    store: Store,
) -> dict[str, int]:
    run_id = uuid.UUID(str(source_run_id))
    selected = urls[: get_settings().max_pages]
    stats = {"requested": len(urls), "fetched": 0, "errors": 0}
    store.update_source_run(
        run_id,
        stats=progress_stats(
            stats,
            stage="crawl",
            status="running",
            message="Crawling URLs",
            percent=0.0,
        ),
    )
    total = len(selected)
    for index, url in enumerate(selected, start=1):
        try:
            body = await fetch_url(url, store=store, source_run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - failures are recorded per URL.
            stats["errors"] += 1
            store.add_fetch(
                source_run_id=run_id,
                url=url,
                body_bytes=None,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            store.update_source_run(
                run_id,
                stats=progress_stats(
                    stats,
                    stage="crawl",
                    status="running",
                    message="Crawling URLs",
                    percent=index / max(total, 1) * 100,
                ),
            )
            continue
        stats["fetched"] += 1
        store.add_fetch(source_run_id=run_id, url=url, body_bytes=body, http_status=200)
        store.update_source_run(
            run_id,
            stats=progress_stats(
                stats,
                stage="crawl",
                status="running",
                message="Crawling URLs",
                percent=index / max(total, 1) * 100,
            ),
        )
    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    store.update_source_run(
        run_id,
        status=status,
        stats=progress_stats(
            stats,
            stage="crawl",
            status="failed" if status == "failed" else "complete",
            message=status,
            percent=100.0,
        ),
        finished=True,
    )
    await enqueue_pipeline_after_crawl(store=store, crawl_run_id=run_id, crawl_status=status)
    return stats
