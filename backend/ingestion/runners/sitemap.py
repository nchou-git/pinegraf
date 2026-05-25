from __future__ import annotations

import uuid
from xml.etree import ElementTree

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion.fetcher import fetch_url
from backend.progress import ProgressEvent, emit_progress


async def run_sitemap(
    source_run_id: uuid.UUID | str,
    sitemap_url: str,
    *,
    store: Store,
) -> dict[str, int]:
    run_id = uuid.UUID(str(source_run_id))
    stats = {"sitemaps": 0, "discovered": 0, "fetched": 0, "errors": 0}
    await emit_progress(run_id, ProgressEvent("crawl", "running", "Reading sitemap", 0.0))
    try:
        urls = await _collect_urls(sitemap_url, stats=stats, seen=set())
    except Exception as exc:  # noqa: BLE001 - failures are stored on the source run.
        stats["errors"] += 1
        store.update_source_run(
            run_id,
            status="failed",
            stats=stats,
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
        await emit_progress(
            run_id,
            ProgressEvent("crawl", "failed", f"{type(exc).__name__}: {exc}", 100.0),
        )
        return stats

    max_pages = get_settings().max_pages
    selected = urls[:max_pages]
    total = len(selected)
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
                ProgressEvent(
                    "crawl", "running", "Crawling sitemap URLs", index / max(total, 1) * 100
                ),
            )
            continue
        stats["fetched"] += 1
        store.add_fetch(source_run_id=run_id, url=url, body_bytes=body, http_status=200)
        await emit_progress(
            run_id,
            ProgressEvent("crawl", "running", "Crawling sitemap URLs", index / max(total, 1) * 100),
        )

    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    store.update_source_run(run_id, status=status, stats=stats, finished=True)
    await emit_progress(
        run_id,
        ProgressEvent("crawl", "failed" if status == "failed" else "complete", status, 100.0),
    )
    return stats


async def _collect_urls(
    sitemap_url: str,
    *,
    stats: dict[str, int],
    seen: set[str],
) -> list[str]:
    if sitemap_url in seen:
        return []
    seen.add(sitemap_url)
    stats["sitemaps"] += 1
    raw = await fetch_url(sitemap_url)
    root = ElementTree.fromstring(raw)
    root_name = _local_name(root.tag)
    if root_name == "sitemapindex":
        urls: list[str] = []
        for loc in _loc_texts(root):
            if stats["discovered"] >= get_settings().max_pages:
                break
            urls.extend(await _collect_urls(loc, stats=stats, seen=seen))
        return urls
    if root_name != "urlset":
        raise ValueError(f"unsupported sitemap root: {root_name}")
    urls = _loc_texts(root)
    remaining = max(get_settings().max_pages - stats["discovered"], 0)
    selected = urls[:remaining]
    stats["discovered"] += len(selected)
    return selected


def _loc_texts(root: ElementTree.Element) -> list[str]:
    values: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) == "loc" and element.text:
            values.append(element.text.strip())
    return [value for value in values if value]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
