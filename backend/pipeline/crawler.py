from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote, urlparse

import httpx

from backend.db.store import Store
from backend.pipeline.page_fetcher import (
    FETCH_TIMEOUT,
    USER_AGENT,
    FetchedPage,
    MockPageFetcher,
    PageFetcher,
    clean_html,
    should_fetch_url,
)
from backend.resolution.entity_resolver import resolve_or_create


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, object]


@dataclass(frozen=True)
class CrawlTask:
    alum_name: str
    entity_id: uuid.UUID
    url: str
    page_index: int
    page_total: int
    depth: int = 0


class SiteCrawler:
    def __init__(
        self,
        *,
        store: Store,
        fetcher: PageFetcher | None = None,
        pages_per_alum: int = 6,
        global_concurrency: int | None = None,
        per_host_concurrency: int | None = None,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.pages_per_alum = pages_per_alum
        self.global_concurrency = global_concurrency or int(
            os.getenv("CRAWL_GLOBAL_CONCURRENCY", "50")
        )
        self.per_host_concurrency = per_host_concurrency or int(
            os.getenv("CRAWL_PER_HOST_CONCURRENCY", "4")
        )
        self._global_semaphore = asyncio.Semaphore(self.global_concurrency)
        self._host_semaphores: dict[str, asyncio.Semaphore] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last_fetch: dict[str, float] = {}
        self._robots_cache: dict[str, float] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._seen_content_hashes: set[str] = set()
        self._seen_content_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    async def run(
        self,
        seed_alumni: list[dict[str, str]],
        emit: Callable[[ProgressEvent], None],
    ) -> None:
        total = len(seed_alumni)
        done = 0
        emit(ProgressEvent("crawl_start", {"overall_total": total, "overall_done": done}))

        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            http2=True,
        ) as client:
            self._client = client
            for index, alum in enumerate(seed_alumni, start=1):
                name = alum["name"].strip()
                class_year = alum.get("class_year", "").strip()
                if not name:
                    continue

                entity_id = self._resolve_seed_entity(name, class_year)
                self.store.enqueue_crawl(name, class_year, depth=0, discovered_via="seed")
                self.store.mark_crawl_status(name, "running", class_year=class_year)
                self.store.upsert_profile(
                    name=name,
                    entity_id=entity_id,
                    class_year=class_year,
                    discovered_via="seed",
                )
                emit(
                    ProgressEvent(
                        "alum_start",
                        {
                            "name": name,
                            "class_year": class_year,
                            "alum_index": index,
                            "overall_total": total,
                            "overall_done": done,
                        },
                    )
                )

                try:
                    urls = self._seed_urls(alum, name=name)
                    fetched = await self._crawl_urls(name, entity_id, urls, emit)
                    self.store.mark_crawl_status(name, "done", class_year=class_year)
                    done += 1
                    emit(
                        ProgressEvent(
                            "alum_done",
                            {
                                "name": name,
                                "class_year": class_year,
                                "pages_fetched": fetched,
                                "page_total": len(urls),
                                "overall_total": total,
                                "overall_done": done,
                            },
                        )
                    )
                except Exception as exc:
                    self.store.mark_crawl_status(name, "failed", class_year=class_year)
                    done += 1
                    emit(
                        ProgressEvent(
                            "alum_done",
                            {
                                "name": name,
                                "class_year": class_year,
                                "error": f"{type(exc).__name__}: {exc}",
                                "overall_total": total,
                                "overall_done": done,
                            },
                        )
                    )
            self._client = None

        emit(ProgressEvent("done", {"overall_total": total, "overall_done": done}))

    async def _crawl_urls(
        self,
        alum_name: str,
        entity_id: uuid.UUID,
        urls: list[str],
        emit: Callable[[ProgressEvent], None],
    ) -> int:
        results = {"fetched": 0}
        queue: asyncio.Queue[CrawlTask] = asyncio.Queue()
        page_total = len(urls)
        for page_index, source_url in enumerate(urls, start=1):
            await queue.put(
                CrawlTask(
                    alum_name=alum_name,
                    entity_id=entity_id,
                    url=source_url.strip(),
                    page_index=page_index,
                    page_total=page_total,
                )
            )

        worker_count = min(max(1, self.global_concurrency), max(1, page_total))
        workers = [
            asyncio.create_task(self._worker(queue, emit, results)) for _ in range(worker_count)
        ]
        await queue.join()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return results["fetched"]

    async def _worker(
        self,
        queue: asyncio.Queue[CrawlTask],
        emit: Callable[[ProgressEvent], None],
        results: dict[str, int],
    ) -> None:
        while True:
            task = await queue.get()
            try:
                if await self._crawl_url(task, emit):
                    results["fetched"] += 1
            finally:
                queue.task_done()


    async def run_sitemap(
        self,
        emit: Callable[[ProgressEvent], None],
        *,
        seed_urls: list[str],
        sitemap_urls: list[str],
        allowed_domains: list[str],
        max_pages: int,
    ) -> None:
        """Whole-site crawl from sitemap URLs and/or seed URLs.

        Not per-alum. Pages get attributed to entities by the parser step.
        """
        import xml.etree.ElementTree as ET

        emit(ProgressEvent("crawl_start", {"overall_total": 0, "overall_done": 0, "max_pages": max_pages}))

        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            http2=True,
        ) as client:
            self._client = client

            # Collect URLs from sitemaps
            urls: list[str] = list(seed_urls)
            for sm_url in sitemap_urls:
                emit(ProgressEvent("sitemap_fetch", {"url": sm_url}))
                try:
                    resp = await client.get(sm_url, timeout=30.0)
                    if resp.status_code != 200:
                        emit(ProgressEvent("sitemap_failed", {"url": sm_url, "status": resp.status_code}))
                        continue
                    root = ET.fromstring(resp.text)
                    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                    # Sitemap index? Recurse one level.
                    for loc in root.findall(".//sm:sitemap/sm:loc", ns):
                        if not loc.text:
                            continue
                        try:
                            sub = await client.get(loc.text.strip(), timeout=30.0)
                            if sub.status_code == 200:
                                sub_root = ET.fromstring(sub.text)
                                for u in sub_root.findall(".//sm:url/sm:loc", ns):
                                    if u.text:
                                        urls.append(u.text.strip())
                        except Exception as exc:
                            emit(ProgressEvent("sitemap_failed", {"url": loc.text, "error": str(exc)}))
                    # Direct URL set
                    for u in root.findall(".//sm:url/sm:loc", ns):
                        if u.text:
                            urls.append(u.text.strip())
                except Exception as exc:
                    emit(ProgressEvent("sitemap_failed", {"url": sm_url, "error": str(exc)}))

            # Filter: allowed domains + dedup + cap
            def allowed(u: str) -> bool:
                if not allowed_domains:
                    return True
                host = urlparse(u).netloc.lower()
                return any(host == d or host.endswith(f".{d}") for d in allowed_domains)

            seen: set[str] = set()
            final: list[str] = []
            for u in urls:
                if u in seen or not allowed(u) or not should_fetch_url(u):
                    continue
                seen.add(u)
                final.append(u)
                if len(final) >= max_pages:
                    break

            emit(ProgressEvent("crawl_planned", {"overall_total": len(final), "overall_done": 0}))

            if not final:
                emit(ProgressEvent("done", {"overall_total": 0, "overall_done": 0, "error": "no URLs to crawl"}))
                return

            # Feed into the existing worker pool
            results = {"fetched": 0, "done": 0}
            queue: asyncio.Queue[CrawlTask] = asyncio.Queue()
            for idx, url in enumerate(final, start=1):
                await queue.put(
                    CrawlTask(
                        alum_name="",
                        entity_id=uuid.UUID(int=0),
                        url=url,
                        page_index=idx,
                        page_total=len(final),
                    )
                )

            async def sitemap_worker() -> None:
                while True:
                    task = await queue.get()
                    try:
                        if await self._crawl_url(task, emit):
                            results["fetched"] += 1
                    finally:
                        results["done"] += 1
                        queue.task_done()

            worker_count = min(max(1, self.global_concurrency), len(final))
            workers = [asyncio.create_task(sitemap_worker()) for _ in range(worker_count)]
            await queue.join()
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

            self._client = None

        emit(ProgressEvent("done", {"overall_total": len(final), "overall_done": results["done"], "fetched_total": results["fetched"]}))

    async def _crawl_url(
        self,
        task: CrawlTask,
        emit: Callable[[ProgressEvent], None],
    ) -> bool:
        latest = self.store.get_latest_raw_page_by_url(task.url)
        page = await self._fetch_page(
            task.url,
            etag=latest.http_etag if latest else None,
            last_modified=latest.http_last_modified if latest else None,
        )
        if page is not None and page.status_code == 304 and latest is not None:
            raw_page = self.store.update_raw_page_fetch_metadata(
                latest.id,
                http_etag=page.etag,
                http_last_modified=page.last_modified,
                http_status=304,
            )
            emit(
                ProgressEvent(
                    "page_skipped",
                    {
                        "name": task.alum_name,
                        "url": task.url,
                        "reason": "not modified",
                        "raw_page_id": raw_page.id if raw_page else latest.id,
                        "page_index": task.page_index,
                        "page_total": task.page_total,
                    },
                )
            )
            return False

        if page is None or not page.text:
            emit(
                ProgressEvent(
                    "page_failed",
                    {
                        "name": task.alum_name,
                        "url": task.url,
                        "page_index": task.page_index,
                        "page_total": task.page_total,
                    },
                )
            )
            return False

        content_hash = _content_sha256(page.raw_html)
        if latest is not None and latest.content_sha256 and latest.content_sha256 == content_hash:
            raw_page = self.store.update_raw_page_fetch_metadata(
                latest.id,
                http_etag=page.etag,
                http_last_modified=page.last_modified,
                http_status=page.status_code,
            )
            emit(
                ProgressEvent(
                    "page_skipped",
                    {
                        "name": task.alum_name,
                        "url": task.url,
                        "reason": "unchanged content",
                        "raw_page_id": raw_page.id if raw_page else latest.id,
                        "page_index": task.page_index,
                        "page_total": task.page_total,
                    },
                )
            )
            return False

        if content_hash and await self._content_hash_seen(content_hash):
            emit(
                ProgressEvent(
                    "page_skipped",
                    {
                        "name": task.alum_name,
                        "url": task.url,
                        "reason": "duplicate content in run",
                        "page_index": task.page_index,
                        "page_total": task.page_total,
                    },
                )
            )
            return False

        raw_page = self.store.save_raw_page(
            alum_name=task.alum_name,
            entity_id=task.entity_id,
            source_url=page.url,
            page_title=page.title,
            page_text=page.text,
            content_sha256=content_hash,
            http_etag=page.etag,
            http_last_modified=page.last_modified,
            http_status=page.status_code,
            raw_html=page.raw_html,
            allow_duplicate_snapshot=latest is not None,
        )
        emit(
            ProgressEvent(
                "page_fetched",
                {
                    "name": task.alum_name,
                    "url": raw_page.source_url,
                    "page_title": raw_page.page_title,
                    "raw_page_id": raw_page.id,
                    "page_index": task.page_index,
                    "page_total": task.page_total,
                },
            )
        )
        return True

    async def _fetch_page(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> FetchedPage | None:
        if self.fetcher is not None:
            return await asyncio.to_thread(
                self.fetcher.fetch,
                url,
                etag=etag,
                last_modified=last_modified,
            )
        return await self._fetch_http(url, etag=etag, last_modified=last_modified)

    async def _fetch_http(
        self,
        url: str,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> FetchedPage | None:
        if self._client is None or not should_fetch_url(url):
            return None
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        host = urlparse(url).netloc.lower()
        await self._load_robots_delay(url)
        async with self._global_semaphore, self._host_semaphore(host):
            await self._pace_host(host)
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError:
                return None
        if response.status_code == 304:
            return FetchedPage(
                url=str(response.url),
                title="",
                text="",
                etag=response.headers.get("etag") or etag,
                last_modified=response.headers.get("last-modified") or last_modified,
                status_code=304,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return None
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return None
        title, text = clean_html(response.text)
        if not text:
            return None
        return FetchedPage(
            url=str(response.url),
            title=title,
            text=text,
            raw_html=response.text,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            status_code=response.status_code,
        )

    async def _load_robots_delay(self, url: str) -> float:
        if self._client is None:
            return 0.0
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        if host in self._robots_cache:
            return self._robots_cache[host]
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            if host in self._robots_cache:
                return self._robots_cache[host]
            robots_url = f"{host_key}/robots.txt"
            delay = 0.0
            try:
                response = await self._client.get(robots_url)
                if response.status_code < 400:
                    delay = _parse_crawl_delay(response.text)
            except httpx.HTTPError:
                delay = 0.0
            self._robots_cache[host] = delay
            return delay

    async def _pace_host(self, host: str) -> None:
        delay = self._robots_cache.get(host) or 0.0
        if delay <= 0:
            return
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            wait_for = delay - (now - self._host_last_fetch.get(host, 0.0))
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._host_last_fetch[host] = time.monotonic()

    def _host_semaphore(self, host: str) -> asyncio.Semaphore:
        semaphore = self._host_semaphores.get(host)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.per_host_concurrency)
            self._host_semaphores[host] = semaphore
        return semaphore

    async def _content_hash_seen(self, content_hash: str) -> bool:
        async with self._seen_content_lock:
            if content_hash in self._seen_content_hashes:
                return True
            self._seen_content_hashes.add(content_hash)
            return False

    def _resolve_seed_entity(self, name: str, class_year: str) -> uuid.UUID:
        context = {"source": "seed_csv"}
        if class_year:
            context["class_year"] = class_year
        with self.store.session() as session:
            entity_id = resolve_or_create(name, session=session, context=context)
            session.commit()
            return entity_id

    def _seed_urls(self, alum: dict[str, str], *, name: str) -> list[str]:
        urls = self._unique_urls(self._iter_seed_urls(alum), limit=self.pages_per_alum)
        if urls or not isinstance(self.fetcher, MockPageFetcher):
            return urls
        slug = quote("-".join(name.lower().split()))
        return [f"https://example.com/{slug}/profile"]

    @staticmethod
    def _iter_seed_urls(alum: dict[str, object]) -> Iterable[str]:
        for key in ("source_url", "profile_url", "url"):
            value = alum.get(key)
            if isinstance(value, str):
                for part in value.replace(";", ",").split(","):
                    cleaned = part.strip()
                    if cleaned:
                        yield cleaned
            elif isinstance(value, Iterable):
                for item in value:
                    cleaned = str(item).strip()
                    if cleaned:
                        yield cleaned

        value = alum.get("urls")
        if isinstance(value, str):
            for part in value.replace(";", ",").split(","):
                cleaned = part.strip()
                if cleaned:
                    yield cleaned
        elif isinstance(value, Iterable):
            for item in value:
                cleaned = str(item).strip()
                if cleaned:
                    yield cleaned

    @staticmethod
    def _unique_urls(urls: Iterable[str], *, limit: int) -> list[str]:
        seen_urls: set[str] = set()
        unique: list[str] = []
        for url_value in urls:
            url = url_value.strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append(url)
            if len(unique) >= limit:
                break
        return unique


def _content_sha256(raw_html: str) -> str | None:
    if not raw_html:
        return None
    return sha256(raw_html.encode("utf-8")).hexdigest()


def _parse_crawl_delay(robots_text: str) -> float:
    for line in robots_text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip().lower() == "crawl-delay":
            try:
                return max(0.0, float(value.strip()))
            except ValueError:
                return 0.0
    return 0.0
