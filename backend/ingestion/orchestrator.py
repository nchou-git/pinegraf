from __future__ import annotations

import uuid
from typing import Any

from backend.db.store import Store
from backend.ingestion.runners.adhoc import run_adhoc
from backend.ingestion.runners.seed import run_seed
from backend.ingestion.runners.sitemap import run_sitemap
from backend.progress import progress_stats


async def run_source_run(
    source_run_id: uuid.UUID | str,
    *,
    store: Store,
) -> None:
    run_id = uuid.UUID(str(source_run_id))
    run = store.get_source_run(run_id)
    if run is None:
        raise ValueError(f"source run not found: {run_id}")
    store.update_source_run(run_id, status="running", clear_finished=True)
    kind = run.kind
    spec: dict[str, Any] = dict(run.spec)
    try:
        if kind == "sitemap":
            source_input = str(spec.get("source_input") or spec["sitemap_url"])
            await run_sitemap(run_id, source_input, store=store)
        elif kind == "seed":
            await run_seed(run_id, str(spec["seed_file_path"]), store=store)
        elif kind == "adhoc":
            urls = [str(url) for url in spec.get("urls", [])]
            await run_adhoc(run_id, urls, store=store)
        else:
            raise ValueError(f"unsupported run kind: {kind}")
    except Exception as exc:  # noqa: BLE001
        store.update_source_run(
            run_id,
            status="failed",
            stats=progress_stats(
                {"errors": 1},
                stage="crawl",
                status="failed",
                message=f"{type(exc).__name__}: {exc}",
                percent=100.0,
            ),
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
