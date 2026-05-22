from __future__ import annotations

from datetime import UTC, datetime

from backend.db.store import Store
from backend.pipeline.crawler import Crawler
from backend.pipeline.page_fetcher import FetchedPage


class NotModifiedFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage:
        self.calls += 1
        if etag == '"v1"':
            return FetchedPage(
                url=url,
                title="",
                text="",
                etag=etag,
                last_modified=last_modified,
                status_code=304,
            )
        html = "<html><body>Jane Doe works at Acme Corp.</body></html>"
        return FetchedPage(
            url=url,
            title="Jane",
            text="Jane Doe works at Acme Corp.",
            raw_html=html,
            etag='"v1"',
            last_modified="Wed, 01 Jan 2026 00:00:00 GMT",
            status_code=200,
        )

    def close(self) -> None:
        return None


class SameBodyFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage:
        del etag, last_modified
        self.calls += 1
        html = "<html><body>Jane Doe works at Acme Corp.</body></html>"
        return FetchedPage(
            url=url,
            title="Jane",
            text="Jane Doe works at Acme Corp.",
            raw_html=html,
            etag=f'"v{self.calls}"',
            status_code=200,
        )

    def close(self) -> None:
        return None


def make_store(tmp_path) -> Store:
    store = Store(f"sqlite:///{tmp_path / 'snapshots.db'}")
    store.init_db()
    return store


def test_304_keeps_same_row_and_bumps_fetched_at(tmp_path) -> None:
    store = make_store(tmp_path)
    fetcher = NotModifiedFetcher()
    crawler = Crawler(store=store, fetcher=fetcher)
    seed = [{"name": "Jane Doe", "class_year": "T'24", "urls": ["https://example.com/jane"]}]

    crawler.run(seed, lambda event: None)
    first = store.list_raw_pages()[0]
    store.update_raw_page_fetch_metadata(
        first.id,
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        http_status=200,
    )

    crawler.run(seed, lambda event: None)

    pages = store.list_raw_pages()
    assert len(pages) == 1
    assert pages[0].id == first.id
    assert pages[0].fetched_at > datetime(2026, 1, 1, tzinfo=UTC)
    assert pages[0].http_status == 304


def test_identical_content_hash_skips_new_snapshot_and_reparse(tmp_path) -> None:
    store = make_store(tmp_path)
    fetcher = SameBodyFetcher()
    crawler = Crawler(store=store, fetcher=fetcher)
    seed = [{"name": "Jane Doe", "class_year": "T'24", "urls": ["https://example.com/jane"]}]

    crawler.run(seed, lambda event: None)
    first = store.list_raw_pages()[0]
    parsed_at = datetime(2026, 1, 2, tzinfo=UTC)
    store.mark_raw_page_parsed(first.id, parsed_at)

    crawler.run(seed, lambda event: None)

    pages = store.list_raw_pages()
    assert len(pages) == 1
    assert pages[0].content_sha256 == first.content_sha256
    assert pages[0].parsed_at == parsed_at
    assert pages[0].http_status == 200


def test_gzipped_html_round_trips(tmp_path) -> None:
    store = make_store(tmp_path)
    html = "<html><body>Snapshot body.</body></html>"

    page = store.save_raw_page(
        alum_name="Jane Doe",
        source_url="https://example.com/jane",
        page_title="Jane",
        page_text="Snapshot body.",
        raw_html=html,
        http_status=200,
    )

    saved = store.list_raw_pages()[0]
    assert saved.raw_html_gz is not None
    assert saved.content_sha256 == page.content_sha256
    assert store.get_raw_page_html(saved.id) == html
