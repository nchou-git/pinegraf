from __future__ import annotations

import asyncio
import json
import random
import re
import time
import uuid
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse
from xml.etree import ElementTree

import httpx
from sqlalchemy.exc import IntegrityError

from backend.config import get_settings
from backend.db.store import Store, content_digest
from backend.ingestion.fetcher import TIMEOUT_SECONDS, fetch_url, robots_allowed, user_agent
from backend.live_logs import append_log
from backend.progress import progress_stats

_SKIP_EXTENSIONS = frozenset(
    [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".webp",
        ".ico",
        ".bmp",
        ".tiff",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".rar",
        ".7z",
        ".mp4",
        ".mp3",
        ".mov",
        ".avi",
        ".wmv",
        ".webm",
        ".ogg",
        ".wav",
        ".flac",
        ".css",
        ".js",
        ".mjs",
        ".map",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".eot",
        ".json",
        ".xml",
        ".rss",
        ".atom",
        ".dmg",
        ".exe",
        ".pkg",
        ".deb",
        ".rpm",
        ".msi",
    ]
)
_DOCUMENT_EXTENSIONS = frozenset(
    [
        ".pdf",
        ".doc",
        ".docx",
        ".csv",
        ".txt",
        ".json",
        ".xml",
        ".rss",
        ".atom",
    ]
)

_TRACKING_PARAMS = frozenset(
    [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "msclkid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "_ga",
    ]
)

ACTIVE_RUN_STATUSES = {"queued", "running"}
HOST_PACING_STATS_INTERVAL = 50
HOST_DELAY_INITIAL_SECONDS = 0.250
HOST_DELAY_FLOOR_SECONDS = 0.100
HOST_DELAY_RATE_LIMIT_MIN_SECONDS = 5.0
HOST_DELAY_CAP_SECONDS = 300.0
HOST_DELAY_JITTER = 0.25


class _HostPacing:
    def __init__(self, delay_seconds: float = HOST_DELAY_INITIAL_SECONDS) -> None:
        self.delay_seconds = delay_seconds
        self.last_fetch_at: float | None = None
        self.last_429_at: str | None = None

    def wait_seconds(self, now: float) -> float:
        if self.last_fetch_at is None:
            return 0.0
        return max(0.0, self.last_fetch_at + self.delay_seconds - now)

    def mark_dispatched(self, now: float) -> None:
        self.last_fetch_at = now

    def record_status(
        self,
        status_code: int,
        *,
        retry_after_seconds: float | None = None,
        now: datetime | None = None,
    ) -> None:
        if 200 <= status_code < 300:
            self.delay_seconds = max(HOST_DELAY_FLOOR_SECONDS, self.delay_seconds * 0.9)
            return
        if status_code not in {429, 503}:
            return
        computed = max(self.delay_seconds * 2, HOST_DELAY_RATE_LIMIT_MIN_SECONDS)
        computed *= random.uniform(1 - HOST_DELAY_JITTER, 1 + HOST_DELAY_JITTER)
        if retry_after_seconds is not None:
            computed = max(computed, retry_after_seconds)
        self.delay_seconds = min(HOST_DELAY_CAP_SECONDS, computed)
        self.last_429_at = (now or datetime.now(UTC)).isoformat()


async def run_sitemap(
    source_run_id: uuid.UUID | str,
    source_input: str,
    *,
    store: Store,
) -> dict[str, int]:
    run_id = uuid.UUID(str(source_run_id))
    run = store.get_source_run(run_id)
    if run is None:
        raise ValueError(f"source run not found: {run_id}")
    source_id = run.source_id
    source = store.get_source(source_id)
    max_depth = source.crawl_depth if source else None
    baseline_fetched = int(source.pages_fetched_total or 0) if source else 0
    baseline_known = int(source.urls_known_total or 0) if source else 0
    stats: dict[str, int] = {"fetched": 0, "errors": 0}
    root_host = _root_host(source_input)
    if not root_host:
        stats["errors"] = 1
        store.update_source_run(
            run_id,
            status="failed",
            stats=progress_stats(
                stats,
                stage="crawl",
                status="failed",
                message="invalid source input",
                percent=100.0,
            ),
            error_message=f"invalid source input: {source_input!r}",
            finished=True,
        )
        return stats

    store.update_source_run(
        run_id,
        stats=progress_stats(
            stats,
            stage="crawl",
            status="running",
            message="Starting crawl",
            percent=0.0,
        ),
    )

    queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
    seen: set[str] = set()
    settings = get_settings()
    cap = settings.max_pages
    concurrency = settings.crawl_concurrency
    liveness_check_interval = settings.crawl_liveness_check_interval
    semaphore = asyncio.Semaphore(concurrency)
    stats_lock = asyncio.Lock()
    seen_lock = asyncio.Lock()
    host_lock = asyncio.Lock()
    host_pacing: dict[str, _HostPacing] = {}
    stop_requested = asyncio.Event()
    highest_percent = 0.0
    reserved = 0
    liveness_checks = 0

    source_url = source_input.strip()
    if "://" not in source_url:
        source_url = f"https://{root_host}/"
    source_path = urlparse(source_url).path.lower()
    seed_method = "sitemap" if source_path.endswith(".xml") or "sitemap" in source_path else "seed"
    seed_urls = [source_url]
    if seed_method == "sitemap":
        try:
            seed_urls = await _collect_from_sitemap(
                source_url, seen=set(), store=store, run_id=run_id
            )
        except Exception:  # noqa: BLE001
            stats["errors"] += 1

    for url in seed_urls:
        _enqueue(url, seed_method, depth=1, queue=queue, seen=seen, root_host=root_host)

    async def reserve_fetch_slot() -> bool:
        nonlocal reserved
        async with stats_lock:
            if stats["fetched"] + reserved >= cap:
                return False
            reserved += 1
            return True

    async def release_fetch_slot() -> None:
        nonlocal reserved
        async with stats_lock:
            reserved = max(0, reserved - 1)

    async def run_is_active() -> bool:
        run = store.get_source_run(run_id)
        return run is not None and run.status in ACTIVE_RUN_STATUSES

    async def check_liveness(*, force: bool = False) -> bool:
        nonlocal liveness_checks
        if stop_requested.is_set():
            return False
        should_check = force
        if not force:
            async with stats_lock:
                liveness_checks += 1
                should_check = liveness_checks % liveness_check_interval == 0
        if should_check and not await run_is_active():
            stop_requested.set()
            return False
        return True

    async def wait_for_host(host: str) -> None:
        while True:
            async with host_lock:
                now = time.monotonic()
                pacing = host_pacing.setdefault(host, _HostPacing())
                delay = pacing.wait_seconds(now)
                if delay <= 0:
                    pacing.mark_dispatched(now)
                    return
            await asyncio.sleep(min(delay, 1.0))

    async def update_host_pacing(host: str, result: _Result) -> None:
        async with host_lock:
            pacing = host_pacing.setdefault(host, _HostPacing())
            previous = pacing.delay_seconds
            pacing.record_status(
                result.status,
                retry_after_seconds=_retry_after_seconds(result.headers.get("retry-after")),
            )
            current = pacing.delay_seconds
        if result.status in {429, 503}:
            _log_fetch_decision(
                f"Throttled {host} after HTTP {result.status}; next delay {current:.2f}s",
                event="host_pacing",
                host=host,
                url=result.final_url,
                http_status=result.status,
                previous_delay_ms=int(previous * 1000),
                delay_ms=int(current * 1000),
            )

    def host_pacing_snapshot() -> dict[str, dict[str, object]]:
        return {
            host: {
                "delay_ms": int(pacing.delay_seconds * 1000),
                "last_429_at": pacing.last_429_at,
            }
            for host, pacing in sorted(host_pacing.items())
        }

    async def worker() -> None:
        nonlocal highest_percent
        while True:
            url, method, depth = await queue.get()
            slot_reserved = False
            try:
                if stop_requested.is_set():
                    continue
                slot_reserved = await reserve_fetch_slot()
                if not slot_reserved:
                    continue
                if not await check_liveness():
                    continue
                host = _host_key(url)
                try:
                    async with semaphore:
                        await wait_for_host(host)
                        result = await _retrieve(url, store=store, run_id=run_id)
                        await update_host_pacing(_host_key(result.final_url), result)
                except Exception as exc:  # noqa: BLE001
                    async with stats_lock:
                        stats["errors"] += 1
                    message = (
                        str(exc)
                        if isinstance(exc, PermissionError)
                        else f"{type(exc).__name__}: {exc}"
                    )
                    _log_fetch_decision(
                        f"Error retrieving {_display_url(url)} — {message}",
                        event="error",
                        url=url,
                        error_type=type(exc).__name__,
                        error=message,
                    )
                    if not await check_liveness(force=True):
                        continue
                    _safe_add_fetch(
                        store,
                        source_run_id=run_id,
                        url=url,
                        body_bytes=None,
                        error_message=message,
                        original_url=url,
                        discovery_method=method,
                    )
                    continue

                final_url = result.final_url
                if not _in_scope(final_url, root_host):
                    _log_fetch_decision(
                        f"Skipped {_display_url(final_url)} — out of scope",
                        event="skipped",
                        reason="out_of_scope",
                        url=final_url,
                        original_url=url,
                    )
                    continue

                if not await check_liveness(force=True):
                    continue
                if not _safe_add_success_fetch(
                    store,
                    source_id=source_id,
                    source_run_id=run_id,
                    url=final_url,
                    body_bytes=result.body,
                    http_status=result.status,
                    content_type=result.content_type,
                    original_url=url,
                    redirect_chain=result.chain if len(result.chain) > 1 else None,
                    discovery_method=method,
                ):
                    continue

                discovered_count = 0
                if result.is_html and (max_depth is None or depth < max_depth):
                    for discovered in _discover_links(result.body, base_url=final_url):
                        async with seen_lock:
                            if _enqueue(
                                discovered,
                                "link_follow",
                                depth=depth + 1,
                                queue=queue,
                                seen=seen,
                                root_host=root_host,
                            ):
                                discovered_count += 1

                async with stats_lock:
                    stats["fetched"] += 1
                    local_known = stats["fetched"] + queue.qsize() + max(reserved - 1, 0)
                    cumulative_floor = max(
                        baseline_known,
                        baseline_fetched + local_known,
                    )
                    should_refresh = stats["fetched"] % HOST_PACING_STATS_INTERVAL == 0
                    if should_refresh:
                        cumulative_fetched, cumulative_known = store.refresh_source_crawl_counters(
                            source_id
                        )
                    else:
                        cumulative_fetched = baseline_fetched + stats["fetched"]
                        cumulative_known = max(baseline_known, cumulative_floor, cumulative_fetched)
                    raw_percent = (
                        round(100.0 * cumulative_fetched / cumulative_known, 1)
                        if cumulative_known
                        else 0.0
                    )
                    displayed_percent = round(min(99.9, max(highest_percent, raw_percent)), 1)
                    highest_percent = displayed_percent
                    fetched = stats["fetched"]
                    pacing = (
                        host_pacing_snapshot()
                        if fetched % HOST_PACING_STATS_INTERVAL == 0
                        else None
                    )
                    stats_snapshot = progress_stats(
                        stats,
                        stage="crawl",
                        status="running",
                        message="Retrieving documents",
                        percent=displayed_percent,
                        data={
                            "fetched": cumulative_fetched,
                            "known": cumulative_known,
                            "raw_percent": raw_percent,
                            **({"host_pacing": pacing} if pacing is not None else {}),
                        },
                    )
                if discovered_count:
                    _log_fetch_decision(
                        (
                            f"Discovered {discovered_count} in-scope links on "
                            f"{_display_url(final_url)}"
                        ),
                        event="discovered",
                        url=final_url,
                        discovered=discovered_count,
                        fetched=fetched,
                        known=cumulative_known,
                    )
                if not await check_liveness(force=True):
                    continue
                store.update_source_run(run_id, stats=stats_snapshot)
                _log_fetch_decision(
                    (
                        f"Retrieved {_display_url(final_url)} — "
                        f"{cumulative_fetched}/{cumulative_known} known ({raw_percent}%)"
                    ),
                    event="retrieved",
                    url=final_url,
                    original_url=url,
                    discovery_method=method,
                    http_status=result.status,
                    fetched=fetched,
                    known=cumulative_known,
                    raw_percent=raw_percent,
                    displayed_percent=displayed_percent,
                )
            finally:
                if slot_reserved:
                    await release_fetch_slot()
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
    await queue.join()
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    if stop_requested.is_set() or not await run_is_active():
        return stats

    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    final_local_known = stats["fetched"] + queue.qsize()
    final_fetched, final_known = store.refresh_source_crawl_counters(
        source_id,
        urls_known_total=final_local_known,
    )
    final_raw_percent = round(100.0 * final_fetched / final_known, 1) if final_known else 0.0
    store.update_source_run(
        run_id,
        status=status,
        stats=progress_stats(
            stats,
            stage="crawl",
            status="failed" if status == "failed" else "complete",
            message=status,
            percent=100.0,
            data={
                "fetched": final_fetched,
                "known": final_known,
                "raw_percent": final_raw_percent,
                "host_pacing": host_pacing_snapshot(),
            },
        ),
        finished=True,
    )
    if status == "complete":
        store.mark_source_full_recrawl_complete(source_id)
    return stats


class _Result(NamedTuple):
    body: bytes
    status: int
    content_type: str | None
    headers: dict[str, str]
    final_url: str
    chain: list[str]
    is_html: bool


async def _retrieve(url: str, *, store: Store, run_id: uuid.UUID) -> _Result:
    if not await robots_allowed(url, store=store, source_run_id=run_id):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    headers = {"User-Agent": user_agent()}
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers=headers, max_redirects=20
    ) as client:
        response = await client.get(url, follow_redirects=True)

    chain = [str(r.url) for r in response.history] + [str(response.url)]
    final_url = str(response.url)
    content_type = response.headers.get("content-type")
    is_html = bool(content_type and "html" in content_type.lower())
    return _Result(
        body=response.content,
        status=response.status_code,
        content_type=content_type,
        headers={key.lower(): value for key, value in response.headers.items()},
        final_url=final_url,
        chain=chain,
        is_html=is_html,
    )


async def _collect_from_sitemap(
    sitemap_url: str,
    *,
    seen: set[str],
    store: Store,
    run_id: uuid.UUID,
) -> list[str]:
    if sitemap_url in seen:
        return []
    seen.add(sitemap_url)
    raw = await fetch_url(sitemap_url, store=store, source_run_id=run_id)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    root_name = _local_name(root.tag)
    locs = [
        element.text.strip()
        for element in root.iter()
        if _local_name(element.tag) == "loc" and element.text and element.text.strip()
    ]
    if root_name == "sitemapindex":
        urls: list[str] = []
        cap = get_settings().max_pages
        for loc in locs:
            remaining = cap - len(urls)
            if remaining <= 0:
                break
            child_urls = await _collect_from_sitemap(loc, seen=seen, store=store, run_id=run_id)
            urls.extend(child_urls[:remaining])
        return urls
    if root_name != "urlset":
        return []
    return locs


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base: str | None = None
        self._in_jsonld = False
        self._jsonld_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "base":
            if self.base is None:
                self.base = attr.get("href")
            return

        if tag in {"a", "area"}:
            href = attr.get("href")
            if href:
                self.links.append(href)
        elif tag in {"iframe", "frame"}:
            src = attr.get("src")
            if src:
                self.links.append(src)
        elif tag == "link":
            rel = (attr.get("rel") or "").lower()
            if any(value in rel for value in ("canonical", "alternate", "next", "prev", "sitemap")):
                href = attr.get("href")
                if href:
                    self.links.append(href)
        elif tag == "meta":
            http_equiv = (attr.get("http-equiv") or "").lower()
            if http_equiv == "refresh":
                content = attr.get("content") or ""
                match = re.search(r"url\s*=\s*([^\s;'\"]+)", content, re.IGNORECASE)
                if match:
                    self.links.append(match.group(1))
        elif tag == "script":
            if (attr.get("type") or "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                payload = json.loads("".join(self._jsonld_buffer))
            except json.JSONDecodeError:
                return
            self.links.extend(_jsonld_urls(payload))


def _jsonld_urls(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key in ("url", "@id", "mainEntityOfPage", "sameAs"):
            value = node.get(key)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
            elif isinstance(value, dict):
                found.extend(_jsonld_urls(value))
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_jsonld_urls(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_jsonld_urls(item))
    return found


def _discover_links(body: bytes, *, base_url: str) -> list[str]:
    text = body.decode("utf-8", errors="replace")
    parser = _LinkCollector()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001
        pass
    effective_base = urljoin(base_url, parser.base) if parser.base else base_url
    resolved: list[str] = []
    for href in parser.links:
        href = href.strip()
        if not href:
            continue
        resolved.append(urljoin(effective_base, href))
    return resolved


def _root_host(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.netloc
    else:
        host = value.split("/", 1)[0]
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host


def _in_scope(url: str, root_host: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host == root_host or host.endswith("." + root_host)


def _host_key(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower().split(":")[0]


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return max(0.0, float(stripped))
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _safe_add_fetch(store: Store, **kwargs: object) -> bool:
    try:
        store.add_fetch(**kwargs)
        return True
    except IntegrityError as exc:
        url = str(kwargs.get("url") or "")
        run_id = kwargs.get("source_run_id")
        append_log(
            "error",
            f"Skipped fetch write for {_display_url(url)}: {type(exc).__name__}: {exc}",
            source_run_id=run_id,
            store=store,
        )
        return False


def _safe_add_success_fetch(
    store: Store,
    *,
    source_id: uuid.UUID,
    source_run_id: uuid.UUID,
    url: str,
    body_bytes: bytes,
    **kwargs: object,
) -> bool:
    digest = content_digest(body_bytes)
    prior = store.latest_successful_fetch_for_url(source_id=source_id, url=url)
    unchanged_since = prior.id if prior is not None and prior.content_hash == digest else None
    return _safe_add_fetch(
        store,
        source_run_id=source_run_id,
        url=url,
        body_bytes=None if unchanged_since is not None else body_bytes,
        content_hash=digest,
        body_unchanged_since=unchanged_since,
        **kwargs,
    )


def _passes_filters(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(tuple(_DOCUMENT_EXTENSIONS)):
        return True
    return not path.endswith(tuple(_SKIP_EXTENSIONS))


def _canonicalize(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    if (scheme == "http" and host.endswith(":80")) or (scheme == "https" and host.endswith(":443")):
        host = host.rsplit(":", 1)[0]
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    cleaned = urlunparse((scheme, host, path, parsed.params, query, ""))
    cleaned, _ = urldefrag(cleaned)
    return cleaned


def _enqueue(
    url: str,
    method: str,
    *,
    depth: int,
    queue: asyncio.Queue[tuple[str, str, int]],
    seen: set[str],
    root_host: str,
) -> bool:
    canonical = _canonicalize(url.strip())
    if canonical in seen:
        _log_fetch_decision(
            f"Skipped {_display_url(canonical)} — already retrieved",
            event="skipped",
            reason="already_retrieved",
            url=canonical,
            discovery_method=method,
        )
        return False
    if not _in_scope(canonical, root_host):
        _log_fetch_decision(
            f"Skipped {_display_url(canonical)} — out of scope",
            event="skipped",
            reason="out_of_scope",
            url=canonical,
            discovery_method=method,
        )
        seen.add(canonical)
        return False
    if not _passes_filters(canonical):
        _log_fetch_decision(
            f"Skipped {_display_url(canonical)} — asset file extension",
            event="skipped",
            reason="asset_file_extension",
            url=canonical,
            discovery_method=method,
        )
        seen.add(canonical)
        return False
    seen.add(canonical)
    queue.put_nowait((canonical, method, depth))
    return True


def _display_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc:
        return urlunparse(("", parsed.netloc, parsed.path or "/", "", parsed.query, ""))
    return url


def _log_fetch_decision(sentence: str, **fields: object) -> None:
    structured = json.dumps(fields, sort_keys=True, default=str)
    append_log("info", f"{sentence} | {structured}")
