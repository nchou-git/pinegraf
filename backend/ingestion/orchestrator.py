from __future__ import annotations

import asyncio
import uuid
from typing import Any

from backend.db.store import Store
from backend.ingestion.runners.adhoc import run_adhoc
from backend.ingestion.runners.seed import run_seed
from backend.ingestion.runners.sitemap import run_sitemap
from backend.progress import ProgressEvent, emit_progress


async def start_run(
    kind: str,
    spec: dict[str, Any],
    triggered_by: str,
    *,
    store: Store,
) -> uuid.UUID:
    source_id = uuid.UUID(str(spec["source_id"]))
    run = store.create_source_run(
        source_id=source_id,
        kind=kind,
        spec=spec,
        triggered_by=triggered_by,
    )
    asyncio.create_task(_dispatch(run.id, kind, spec, store=store))
    return run.id


async def _dispatch(
    run_id: uuid.UUID,
    kind: str,
    spec: dict[str, Any],
    *,
    store: Store,
) -> None:
    try:
        if kind == "sitemap":
            await run_sitemap(run_id, str(spec.get("source_input") or spec["sitemap_url"]), store=store)
        elif kind == "seed":
            await run_seed(run_id, str(spec["seed_file_path"]), store=store)
        elif kind == "adhoc":
            urls = [str(url) for url in spec.get("urls", [])]
            await run_adhoc(run_id, urls, store=store)
        else:
            raise ValueError(f"unsupported run kind: {kind}")
    except Exception as exc:  # noqa: BLE001 - background failures belong on source_runs.
        store.update_source_run(
            run_id,
            status="failed",
            stats={"errors": 1},
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
        await emit_progress(
            run_id,
            ProgressEvent("crawl", "failed", f"{type(exc).__name__}: {exc}", 100.0),
        )
