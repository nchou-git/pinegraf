from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import quote

from backend.db.store import Store
from backend.pipeline.page_fetcher import MockPageFetcher, PageFetcher


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

            self.store.enqueue_crawl(name, class_year, depth=0, discovered_via="seed")
            self.store.mark_crawl_status(name, "running", class_year=class_year)
            self.store.upsert_profile(
                name=name,
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
                fetched = self._crawl_urls(name, urls, emit)
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
        urls: list[str],
        emit: Callable[[ProgressEvent], None],
    ) -> int:
        fetched = 0
        page_total = len(urls)
        for page_index, source_url in enumerate(urls, start=1):
            url = source_url.strip()
            if self.store.raw_page_exists(alum_name, url):
                emit(
                    ProgressEvent(
                        "page_skipped",
                        {
                            "name": alum_name,
                            "url": url,
                            "reason": "already stored",
                            "page_index": page_index,
                            "page_total": page_total,
                        },
                    )
                )
                continue

            page = self.fetcher.fetch(url)
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

            raw_page = self.store.save_raw_page(
                alum_name=alum_name,
                source_url=page.url,
                page_title=page.title,
                page_text=page.text,
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
