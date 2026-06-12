from __future__ import annotations

import pytest

from backend.main import SourceCreate, normalize_identifier


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://tuck.dartmouth.edu/", "tuck.dartmouth.edu"),
        ("tuck.dartmouth.edu", "tuck.dartmouth.edu"),
        ("http://tuck.dartmouth.edu", "tuck.dartmouth.edu"),
        ("HTTPS://WWW.TUCK.DARTMOUTH.EDU/path?q=1#frag", "tuck.dartmouth.edu"),
        ("  www.TUCK.dartmouth.edu/anything  ", "tuck.dartmouth.edu"),
    ],
)
def test_normalize_domain_identifier_variants(raw: str, expected: str) -> None:
    assert normalize_identifier("domain", raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://tuck.dartmouth.edu/sitemap.xml", "https://tuck.dartmouth.edu/sitemap.xml"),
        ("tuck.dartmouth.edu/sitemap.xml", "https://tuck.dartmouth.edu/sitemap.xml"),
        (
            "HTTPS://WWW.Tuck.Dartmouth.EDU/sitemap_index.xml#frag",
            "https://tuck.dartmouth.edu/sitemap_index.xml",
        ),
        ("https://tuck.dartmouth.edu/news/sitemap", "https://tuck.dartmouth.edu/news/sitemap"),
        (
            "https://tuck.dartmouth.edu/feeds/posts.xml?utm_source=x",
            "https://tuck.dartmouth.edu/feeds/posts.xml",
        ),
    ],
)
def test_normalize_domain_identifier_preserves_sitemap_urls(raw: str, expected: str) -> None:
    assert normalize_identifier("domain", raw) == expected


def test_website_and_sitemap_identifiers_for_same_host_are_distinct() -> None:
    assert normalize_identifier("domain", "tuck.dartmouth.edu") == "tuck.dartmouth.edu"
    assert (
        normalize_identifier("domain", "https://tuck.dartmouth.edu/sitemap.xml")
        == "https://tuck.dartmouth.edu/sitemap.xml"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://tuck.dartmouth.edu/news/story?utm_source=x#bio",
            "https://tuck.dartmouth.edu/news/story",
        ),
        ("tuck.dartmouth.edu/news/story", "https://tuck.dartmouth.edu/news/story"),
        ("HTTPS://WWW.Tuck.Dartmouth.EDU", "https://tuck.dartmouth.edu/"),
    ],
)
def test_normalize_domain_identifier_preserves_page_urls_in_website_mode(
    raw: str,
    expected: str,
) -> None:
    assert normalize_identifier("domain", raw, crawl_depth=1) == expected


def test_website_mode_keeps_same_domain_pages_distinct() -> None:
    assert normalize_identifier(
        "domain", "https://tuck.dartmouth.edu/news/a", crawl_depth=1
    ) != normalize_identifier("domain", "https://tuck.dartmouth.edu/news/b", crawl_depth=1)


def test_source_create_normalizes_domain_identifier() -> None:
    payload = SourceCreate(kind="domain", identifier="HTTPS://WWW.TUCK.DARTMOUTH.EDU/path")
    assert payload.identifier == "tuck.dartmouth.edu"


def test_source_create_preserves_page_url_with_crawl_depth() -> None:
    payload = SourceCreate(
        kind="domain",
        identifier="HTTPS://WWW.TUCK.DARTMOUTH.EDU/path?q=1#frag",
        crawl_depth=1,
    )
    assert payload.identifier == "https://tuck.dartmouth.edu/path"
    assert payload.crawl_depth == 1


def test_source_create_rejects_empty_domain_identifier() -> None:
    with pytest.raises(ValueError):
        SourceCreate(kind="domain", identifier="  ")


def test_normalize_file_identifier_strips_whitespace_only() -> None:
    assert normalize_identifier("file", "  Alumni Upload.xlsx  ") == "Alumni Upload.xlsx"


def test_upsert_source_deduplicates_normalized_domain_identifiers(store) -> None:
    first = store.upsert_source(
        kind="domain",
        identifier="https://tuck.dartmouth.edu/",
        display_name="First",
    )
    second = store.upsert_source(
        kind="domain",
        identifier="HTTP://WWW.TUCK.DARTMOUTH.EDU/path?q=1",
        display_name="Second",
    )

    assert second.id == first.id
    assert second.identifier == "tuck.dartmouth.edu"
    assert second.display_name == "Second"
    assert store.table_counts(["sources"])["sources"] == 1


def test_upsert_source_keeps_depth_limited_pages_distinct_under_shared_label(store) -> None:
    first = store.upsert_source(
        kind="domain",
        identifier="https://tuck.dartmouth.edu/news/a",
        display_name="Tuck",
        crawl_depth=1,
    )
    second = store.upsert_source(
        kind="domain",
        identifier="https://tuck.dartmouth.edu/news/b",
        display_name="Tuck",
        crawl_depth=2,
    )
    full = store.upsert_source(
        kind="domain",
        identifier="tuck.dartmouth.edu",
        display_name="Tuck",
        crawl_depth=None,
    )

    assert first.id != second.id
    assert first.id != full.id
    assert first.display_name == second.display_name == full.display_name == "Tuck"
    assert first.identifier == "https://tuck.dartmouth.edu/news/a"
    assert second.identifier == "https://tuck.dartmouth.edu/news/b"
    assert full.identifier == "tuck.dartmouth.edu"
    assert first.crawl_depth == 1
    assert second.crawl_depth == 2
    assert full.crawl_depth is None
