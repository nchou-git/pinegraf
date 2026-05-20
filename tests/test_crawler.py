from __future__ import annotations

from backend.db.store import Store
from backend.pipeline.crawler import Crawler, ProgressEvent
from backend.pipeline.page_fetcher import FetchedPage
from backend.pipeline.search import SearchClient, SearchResult


class FakeSearchClient(SearchClient):
    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        del class_year
        slug = name.lower().replace(" ", "-")
        return [
            SearchResult("One", f"https://example.com/{slug}/one", ""),
            SearchResult("Duplicate", f"https://example.com/{slug}/one", ""),
            SearchResult("Two", f"https://example.com/{slug}/two", ""),
        ]


class FakeFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        return FetchedPage(url=url, title=url.rsplit("/", 1)[-1], text=f"Text for {url}")

    def close(self) -> None:
        return None


def test_crawler_saves_raw_pages_and_dedupes_urls(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'crawl.db'}")
    store.init_db()
    fetcher = FakeFetcher()
    events: list[ProgressEvent] = []
    crawler = Crawler(
        store=store,
        search_client=FakeSearchClient(),
        fetcher=fetcher,  # type: ignore[arg-type]
    )
    seed = [{"name": "Jane Doe", "class_year": "T'24"}]

    crawler.run(seed, events.append)

    pages = store.list_raw_pages()
    assert len(pages) == 2
    assert len(fetcher.urls) == 2
    assert {page.source_url for page in pages} == {
        "https://example.com/jane-doe/one",
        "https://example.com/jane-doe/two",
    }
    assert any(event.kind == "page_fetched" for event in events)

    crawler.run(seed, events.append)

    assert len(store.list_raw_pages()) == 2
    assert len(fetcher.urls) == 2
    assert any(event.kind == "page_skipped" for event in events)
