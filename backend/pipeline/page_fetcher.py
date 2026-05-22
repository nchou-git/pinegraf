from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

FETCH_TIMEOUT = 15.0
FETCH_DELAY_SECONDS = 2.0
FETCH_RETRY_BACKOFF_SECONDS = (1.0, 3.0)
USER_AGENT = "PinegrafBot/0.2 (crawl; contact: nchou-git)"
SKIP_FETCH_DOMAINS = {"linkedin.com", "www.linkedin.com"}
NOISY_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "aside", "form", "iframe")


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
    raw_html: str = ""
    etag: str | None = None
    last_modified: str | None = None
    status_code: int = 200


def should_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    blocked = any(host == domain or host.endswith(f".{domain}") for domain in SKIP_FETCH_DOMAINS)
    return parsed.scheme in {"http", "https"} and not blocked


def clean_html(html: str) -> tuple[str, str]:
    tree = HTMLParser(html)
    for node in tree.css(", ".join(NOISY_TAGS)):
        node.decompose()
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    text_node = tree.body or tree.root
    text = text_node.text(separator=" ", strip=True) if text_node else ""
    return title, " ".join(text.split())


def extract_links(html: str, base_url: str) -> list[str]:
    tree = HTMLParser(html)
    links: list[str] = []
    for node in tree.css("a[href]"):
        href = node.attributes.get("href", "").strip()
        if not href:
            continue
        absolute_url = urljoin(base_url, href)
        if should_fetch_url(absolute_url):
            links.append(absolute_url)
    return links


def _is_transient_http_error(exc: httpx.HTTPError) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


class PageFetcher:
    def __init__(
        self,
        *,
        delay: float = FETCH_DELAY_SECONDS,
        retry_backoff_seconds: Sequence[float] = FETCH_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self.delay = delay
        self.retry_backoff_seconds = tuple(retry_backoff_seconds)
        self._client = httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage | None:
        if not should_fetch_url(url):
            return None

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        attempts = len(self.retry_backoff_seconds) + 1
        for attempt in range(attempts):
            try:
                response = self._client.get(url, headers=headers)
                if response.status_code == 304:
                    return FetchedPage(
                        url=str(response.url),
                        title="",
                        text="",
                        raw_html="",
                        etag=response.headers.get("etag") or etag,
                        last_modified=response.headers.get("last-modified") or last_modified,
                        status_code=304,
                    )
                response.raise_for_status()
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
            except httpx.HTTPError as exc:
                if attempt >= attempts - 1 or not _is_transient_http_error(exc):
                    return None
                time.sleep(self.retry_backoff_seconds[attempt])
            except Exception:
                return None
            finally:
                if self.delay:
                    time.sleep(self.delay)
        return None

    def close(self) -> None:
        self._client.close()


class MockPageFetcher(PageFetcher):
    def __init__(self) -> None:
        self.delay = 0.0
        self.retry_backoff_seconds = ()

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage | None:
        del etag, last_modified
        if not url or not should_fetch_url(url):
            return None
        slug = url.rstrip("/").split("/")[-1]
        title = slug.replace("-", " ").title()
        text = (
            f"{title}. The alumnus is a Senior Manager at Acme Corp. "
            "Previously worked at Beta Inc and Gamma LLC. Dartmouth Tuck MBA. "
            "Errik Anderson and Daniella Reichstetter worked together on the Gyrobike "
            "first-year project at Tuck."
        )
        raw_html = f"<html><head><title>{title}</title></head><body>{text}</body></html>"
        return FetchedPage(url=url, title=title, text=text, raw_html=raw_html, status_code=200)

    def close(self) -> None:
        return None


class FixturePageFetcher(PageFetcher):
    def __init__(self, fixtures_dir: str | Path) -> None:
        self.delay = 0.0
        self.retry_backoff_seconds = ()
        self.fixtures_dir = Path(fixtures_dir)
        self._pages = self._load_fixtures(self.fixtures_dir)

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchedPage | None:
        del etag, last_modified
        page = self._pages.get(url)
        if page is None:
            return None
        return page

    def close(self) -> None:
        return None

    @staticmethod
    def _load_fixtures(fixtures_dir: Path) -> dict[str, FetchedPage]:
        pages: dict[str, FetchedPage] = {}
        for path in sorted(fixtures_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            url = str(payload.get("url", "")).strip()
            text = str(payload.get("text", "")).strip()
            if not url or not text:
                continue
            pages[url] = FetchedPage(
                url=url,
                title=str(payload.get("title", "")).strip(),
                text=text,
                raw_html=str(payload.get("raw_html", "")).strip()
                or f"<html><body>{text}</body></html>",
                etag=payload.get("etag"),
                last_modified=payload.get("last_modified"),
                status_code=int(payload.get("status_code", 200)),
            )
        return pages
