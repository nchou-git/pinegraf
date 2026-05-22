from __future__ import annotations

from backend.db.store import Store
from backend.pipeline.crawler import Crawler, ProgressEvent
from backend.pipeline.page_fetcher import FetchedPage


class FakeFetcher:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage:
        del etag, last_modified
        self.urls.append(url)
        text = f"Text for {url}"
        return FetchedPage(
            url=url,
            title=url.rsplit("/", 1)[-1],
            text=text,
            raw_html=f"<html><body>{text}</body></html>",
        )

    def close(self) -> None:
        return None


def test_crawler_saves_raw_pages_and_dedupes_urls(tmp_path) -> None:
    store = Store(f"sqlite:///{tmp_path / 'crawl.db'}")
    store.init_db()
    fetcher = FakeFetcher()
    events: list[ProgressEvent] = []
    crawler = Crawler(
        store=store,
        fetcher=fetcher,  # type: ignore[arg-type]
    )
    seed = [
        {
            "name": "Jane Doe",
            "class_year": "T'24",
            "urls": [
                "https://example.com/jane-doe/one",
                "https://example.com/jane-doe/one",
                "https://example.com/jane-doe/two",
            ],
        }
    ]

    crawler.run(seed, events.append)

    pages = store.list_raw_pages()
    assert len(pages) == 2
    assert all(page.entity_id is not None for page in pages)
    assert len({page.entity_id for page in pages}) == 1
    assert len(fetcher.urls) == 2
    assert {page.source_url for page in pages} == {
        "https://example.com/jane-doe/one",
        "https://example.com/jane-doe/two",
    }
    assert any(event.kind == "page_fetched" for event in events)

    crawler.run(seed, events.append)

    assert len(store.list_raw_pages()) == 2
    assert len(fetcher.urls) == 4
    assert any(event.kind == "page_skipped" for event in events)
