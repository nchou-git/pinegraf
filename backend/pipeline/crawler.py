from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from backend.db.store import Store
from backend.pipeline.page_fetcher import PageFetcher
from backend.pipeline.search import SearchClient, SearchResult


@dataclass
class ProgressEvent:
    kind: str
    data: dict[str, object]


class Crawler:
    def __init__(
        self,
        *,
        store: Store,
        search_client: SearchClient,
        fetcher: PageFetcher,
        pages_per_alum: int = 6,
    ) -> None:
        self.store = store
        self.search_client = search_client
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
                results = self._unique_results(
                    self.search_client.search_person(name, class_year),
                    limit=self.pages_per_alum,
                )
                fetched = self._crawl_results(name, results, emit)
                self.store.mark_crawl_status(name, "done", class_year=class_year)
                done += 1
                emit(
                    ProgressEvent(
                        "alum_done",
                        {
                            "name": name,
                            "class_year": class_year,
                            "pages_fetched": fetched,
                            "page_total": len(results),
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

    def _crawl_results(
        self,
        alum_name: str,
        results: list[SearchResult],
        emit: Callable[[ProgressEvent], None],
    ) -> int:
        fetched = 0
        page_total = len(results)
        for page_index, result in enumerate(results, start=1):
            url = result.link.strip()
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
                page_title=page.title or result.title,
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

    @staticmethod
    def _unique_results(results: Iterable[SearchResult], *, limit: int) -> list[SearchResult]:
        seen_urls: set[str] = set()
        unique: list[SearchResult] = []
        for result in results:
            url = result.link.strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            unique.append(result)
            if len(unique) >= limit:
                break
        return unique
