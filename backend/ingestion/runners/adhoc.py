from __future__ import annotations

import uuid

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion.fetcher import fetch_url


async def run_adhoc(
    source_run_id: uuid.UUID | str,
    urls: list[str],
    *,
    store: Store,
) -> dict[str, int]:
    run_id = _as_uuid(source_run_id)
    stats = {"requested": len(urls), "fetched": 0, "errors": 0}
    for url in urls[: get_settings().max_pages]:
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
            continue
        stats["fetched"] += 1
        store.add_fetch(source_run_id=run_id, url=url, body_bytes=body, http_status=200)
    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    store.update_source_run(run_id, status=status, stats=stats, finished=True)
    return stats


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
