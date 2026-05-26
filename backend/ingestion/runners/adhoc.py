from __future__ import annotations

import uuid

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion.fetcher import fetch_url
from backend.progress import ProgressEvent, emit_progress


async def run_adhoc(
    source_run_id: uuid.UUID | str,
    urls: list[str],
    *,
    store: Store,
) -> dict[str, int]:
    run_id = uuid.UUID(str(source_run_id))
    selected = urls[: get_settings().max_pages]
    stats = {"requested": len(urls), "fetched": 0, "errors": 0}
    await emit_progress(run_id, ProgressEvent("crawl", "running", "Crawling URLs", 0.0))
    total = len(selected)
    last_running_stats_fetched = 0
    for index, url in enumerate(selected, start=1):
        try:
            body = await fetch_url(url)
        except Exception as exc:  # noqa: BLE001 - failures are recorded per URL.
            stats["errors"] += 1
            store.add_fetch(
                source_run_id=run_id,
                url=url,
                body_bytes=None,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            await emit_progress(
                run_id,
                ProgressEvent("crawl", "running", "Crawling URLs", index / max(total, 1) * 100),
            )
            if (
                stats["fetched"] > 0
                and stats["fetched"] % 10 == 0
                and stats["fetched"] != last_running_stats_fetched
            ):
                store.update_source_run(run_id, stats=stats)
                last_running_stats_fetched = stats["fetched"]
            continue
        stats["fetched"] += 1
        store.add_fetch(source_run_id=run_id, url=url, body_bytes=body, http_status=200)
        await emit_progress(
            run_id,
            ProgressEvent("crawl", "running", "Crawling URLs", index / max(total, 1) * 100),
        )
        if (
            stats["fetched"] > 0
            and stats["fetched"] % 10 == 0
            and stats["fetched"] != last_running_stats_fetched
        ):
            store.update_source_run(run_id, stats=stats)
            last_running_stats_fetched = stats["fetched"]
    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    store.update_source_run(run_id, status=status, stats=stats, finished=True)
    await emit_progress(
        run_id,
        ProgressEvent("crawl", "failed" if status == "failed" else "complete", status, 100.0),
    )
    return stats
