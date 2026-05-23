from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

FETCH_TIMEOUT = 15.0
FETCH_DELAY_SECONDS = 2.0
FETCH_RETRY_BACKOFF_SECONDS = (1.0, 3.0)
USER_AGENT = "PinegrafBot/0.2 (crawl; contact: nchou-git)"
SKIP_FETCH_DOMAINS = {"linkedin.com", "www.linkedin.com"}
NOISY_SELECTORS = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "noscript",
    "aside",
    "form",
    "iframe",
    "[role='navigation']",
    "[id^='form_']",
    "#header",
    "#headerWrap",
    "#drawerNav",
    "#footer",
    "#catMenu",
    ".hidden-nav-form",
    ".category-tabs",
    ".jumpMenu",
    ".blog-search",
    ".sidebar",
    ".social",
    ".socialMobile",
    ".social-bottom",
)
BOILERPLATE_SCAN_CHARS = 2000
BOILERPLATE_NGRAM_SIZE = 5
BOILERPLATE_FINGERPRINT_NGRAMS = 10
MIN_COMMON_BOILERPLATE_CHARS = 50
MAX_BOILERPLATE_PATTERNS = 4
BOILERPLATE_PATTERN_SEPARATOR = "\n<<<PINEGRAF_BOILERPLATE_PATTERN>>>\n"


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str
    raw_html: str = ""
    etag: str | None = None
    last_modified: str | None = None
    status_code: int = 200


@dataclass(frozen=True)
class TextBoilerplate:
    prefix: str = ""
    suffix: str = ""


def should_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    blocked = any(host == domain or host.endswith(f".{domain}") for domain in SKIP_FETCH_DOMAINS)
    return parsed.scheme in {"http", "https"} and not blocked


def clean_html(html: str, *, boilerplate: TextBoilerplate | None = None) -> tuple[str, str]:
    tree = HTMLParser(html)
    for node in tree.css(", ".join(NOISY_SELECTORS)):
        node.decompose()
    title_node = tree.css_first("title")
    title = title_node.text(strip=True) if title_node else ""
    text_node = tree.body or tree.root
    text = text_node.text(separator=" ", strip=True) if text_node else ""
    cleaned = " ".join(text.split())
    cleaned = " ".join(re.sub(r"\bLoading\.\.\.", " ", cleaned).split())
    if boilerplate is not None:
        cleaned = strip_boilerplate(cleaned, boilerplate)
    return title, cleaned


def strip_boilerplate(text: str, boilerplate: TextBoilerplate) -> str:
    stripped = text
    for prefix in _boilerplate_patterns(boilerplate.prefix):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
    for suffix in _boilerplate_patterns(boilerplate.suffix):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
    return stripped


def build_boilerplate_model(texts: Sequence[str]) -> TextBoilerplate:
    if len(texts) < 2:
        return TextBoilerplate()
    prefix = BOILERPLATE_PATTERN_SEPARATOR.join(_dominant_common_edges(texts, edge="prefix"))
    suffix = BOILERPLATE_PATTERN_SEPARATOR.join(_dominant_common_edges(texts, edge="suffix"))
    return TextBoilerplate(prefix=prefix, suffix=suffix)


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
        boilerplate_provider: Callable[[str], TextBoilerplate | None] | None = None,
    ) -> None:
        self.delay = delay
        self.retry_backoff_seconds = tuple(retry_backoff_seconds)
        self.boilerplate_provider = boilerplate_provider
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
                response_url = str(response.url)
                title, text = clean_html(
                    response.text,
                    boilerplate=self._boilerplate_for_url(response_url),
                )
                if not text:
                    return None
                return FetchedPage(
                    url=response_url,
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

    def _boilerplate_for_url(self, url: str) -> TextBoilerplate | None:
        if self.boilerplate_provider is None:
            return None
        host = urlparse(url).netloc.lower()
        if not host:
            return None
        return self.boilerplate_provider(host)


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


def _dominant_common_edge(texts: Sequence[str], *, edge: str) -> str:
    keyed_texts: dict[str, list[str]] = {}
    for text in texts:
        segment = (
            text[:BOILERPLATE_SCAN_CHARS] if edge == "prefix" else text[-BOILERPLATE_SCAN_CHARS:]
        )
        fingerprint = _ngram_fingerprint(segment, edge=edge)
        if not fingerprint:
            continue
        keyed_texts.setdefault(fingerprint, []).append(text)
    if not keyed_texts:
        return ""

    fingerprint, grouped = Counter(
        {fingerprint: len(grouped) for fingerprint, grouped in keyed_texts.items()}
    ).most_common(1)[0]
    if grouped <= len(texts) / 2:
        return ""

    common = (
        _common_prefix(keyed_texts[fingerprint])
        if edge == "prefix"
        else _common_suffix(keyed_texts[fingerprint])
    )
    return _trim_common_text(common, edge=edge, texts=keyed_texts[fingerprint])


def _dominant_common_edges(texts: Sequence[str], *, edge: str) -> list[str]:
    working_texts = list(texts)
    patterns: list[str] = []
    for _ in range(MAX_BOILERPLATE_PATTERNS):
        pattern = _dominant_common_edge(working_texts, edge=edge)
        if not pattern or pattern in patterns:
            break
        patterns.append(pattern)
        working_texts = [_strip_edge(text, pattern, edge=edge) for text in working_texts]
    return patterns


def _strip_edge(text: str, pattern: str, *, edge: str) -> str:
    if edge == "prefix" and text.startswith(pattern):
        return text[len(pattern) :].strip()
    if edge == "suffix" and text.endswith(pattern):
        return text[: -len(pattern)].strip()
    return text


def _boilerplate_patterns(value: str) -> list[str]:
    return [
        pattern.strip() for pattern in value.split(BOILERPLATE_PATTERN_SEPARATOR) if pattern.strip()
    ]


def _ngram_fingerprint(segment: str, *, edge: str) -> str:
    tokens = re.findall(r"[a-z0-9']+", segment.lower())
    if len(tokens) < BOILERPLATE_NGRAM_SIZE:
        normalized = " ".join(segment.lower().split())
        return sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
    ngrams = [
        " ".join(tokens[index : index + BOILERPLATE_NGRAM_SIZE])
        for index in range(len(tokens) - BOILERPLATE_NGRAM_SIZE + 1)
    ]
    ngrams = (
        ngrams[:BOILERPLATE_FINGERPRINT_NGRAMS]
        if edge == "prefix"
        else ngrams[-BOILERPLATE_FINGERPRINT_NGRAMS:]
    )
    return sha256("\n".join(ngrams).encode("utf-8")).hexdigest()


def _common_prefix(texts: Iterable[str]) -> str:
    iterator = iter(texts)
    try:
        prefix = next(iterator)
    except StopIteration:
        return ""
    for text in iterator:
        while prefix and not text.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _common_suffix(texts: Iterable[str]) -> str:
    reversed_texts = [text[::-1] for text in texts]
    return _common_prefix(reversed_texts)[::-1]


def _trim_common_text(text: str, *, edge: str, texts: Sequence[str]) -> str:
    cleaned = text.strip()
    if len(cleaned) < MIN_COMMON_BOILERPLATE_CHARS:
        return ""
    if _edge_ends_on_boundary(text, edge=edge, texts=texts):
        return cleaned
    if edge == "prefix":
        return cleaned.rsplit(" ", 1)[0].strip()
    return cleaned.split(" ", 1)[-1].strip()


def _edge_ends_on_boundary(common: str, *, edge: str, texts: Sequence[str]) -> bool:
    if edge == "prefix":
        if common[-1:].isspace():
            return True
        for text in texts:
            if len(text) <= len(common):
                continue
            if not _is_boundary_char(text[len(common)]):
                return False
        return True

    if common[:1].isspace():
        return True
    for text in texts:
        start = len(text) - len(common)
        if start <= 0:
            continue
        if not _is_boundary_char(text[start - 1]):
            return False
    return True


def _is_boundary_char(value: str) -> bool:
    return value.isspace() or value in {".", ",", ";", ":", "|", "/", "-", ")", "]", "}"}


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
