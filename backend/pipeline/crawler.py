from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote

from backend.db.store import Store
from backend.pipeline.page_fetcher import MockPageFetcher, PageFetcher
from backend.resolution.entity_resolver import resolve_or_create


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, object]


class Crawler:
    """Deprecated sync crawler wrapper; use SiteCrawler for new crawling code."""

    def __init__(
        self,
        *,
        store: Store,
        fetcher: PageFetcher,
        pages_per_alum: int = 6,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.pages_per_alum = pages_per_alum

    def run(
        self,
        seed_alumni: list[dict[str, str]],
        emit: Callable[[ProgressEvent], None],
    ) -> None:
        total = len(seed_alumni)
        done = 0
        emit(ProgressEvent("crawl_start", {"overall_total": total, "overall_done": done}))

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
                fetched = self._crawl_urls(name, entity_id, urls, emit)
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

        emit(ProgressEvent("done", {"overall_total": total, "overall_done": done}))

    def _crawl_urls(
        self,
        alum_name: str,
        entity_id: uuid.UUID,
        urls: list[str],
        emit: Callable[[ProgressEvent], None],
    ) -> int:
        fetched = 0
        page_total = len(urls)
        for page_index, source_url in enumerate(urls, start=1):
            url = source_url.strip()
            latest = self.store.get_latest_raw_page_by_url(url)
            page = self.fetcher.fetch(
                url,
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
                            "name": alum_name,
                            "url": url,
                            "reason": "not modified",
                            "raw_page_id": raw_page.id if raw_page else latest.id,
                            "page_index": page_index,
                            "page_total": page_total,
                        },
                    )
                )
                continue

            if page is None or not page.text:
                emit(
                    ProgressEvent(
                        "page_failed",
                        {
                            "name": alum_name,
                            "url": url,
                            "page_index": page_index,
                            "page_total": page_total,
                        },
                    )
                )
                continue

            content_hash = _content_sha256(page.raw_html)
            if (
                latest is not None
                and latest.content_sha256
                and latest.content_sha256 == content_hash
            ):
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
                            "name": alum_name,
                            "url": url,
                            "reason": "unchanged content",
                            "raw_page_id": raw_page.id if raw_page else latest.id,
                            "page_index": page_index,
                            "page_total": page_total,
                        },
                    )
                )
                continue

            raw_page = self.store.save_raw_page(
                alum_name=alum_name,
                entity_id=entity_id,
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
            fetched += 1
            emit(
                ProgressEvent(
                    "page_fetched",
                    {
                        "name": alum_name,
                        "url": raw_page.source_url,
                        "page_title": raw_page.page_title,
                        "raw_page_id": raw_page.id,
                        "page_index": page_index,
                        "page_total": page_total,
                    },
                )
            )
        return fetched

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
