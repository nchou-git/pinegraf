from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

FETCH_TIMEOUT = 15.0
FETCH_DELAY_SECONDS = 2.0
FETCH_RETRY_BACKOFF_SECONDS = (1.0, 3.0)
USER_AGENT = "PinegrafBot/0.2 (crawl; contact: nchou-git)"
SKIP_FETCH_DOMAINS = {"linkedin.com", "www.linkedin.com"}
NOISY_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "aside")


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str


def should_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    blocked = any(host == domain or host.endswith(f".{domain}") for domain in SKIP_FETCH_DOMAINS)
    return parsed.scheme in {"http", "https"} and not blocked


def clean_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISY_TAGS):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    text = soup.get_text(separator=" ", strip=True)
    return title, " ".join(text.split())


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

    def fetch(self, url: str) -> FetchedPage | None:
        if not should_fetch_url(url):
            return None

        attempts = len(self.retry_backoff_seconds) + 1
        for attempt in range(attempts):
            try:
                response = self._client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type and "text" not in content_type:
                    return None
                title, text = clean_html(response.text)
                if not text:
                    return None
                return FetchedPage(url=url, title=title, text=text)
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

    def fetch(self, url: str) -> FetchedPage | None:
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
        return FetchedPage(url=url, title=title, text=text)

    def close(self) -> None:
        return None


class FixturePageFetcher(PageFetcher):
    def __init__(self, fixtures_dir: str | Path) -> None:
        self.delay = 0.0
        self.retry_backoff_seconds = ()
        self.fixtures_dir = Path(fixtures_dir)
        self._pages = self._load_fixtures(self.fixtures_dir)

    def fetch(self, url: str) -> FetchedPage | None:
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
            )
        return pages
