from __future__ import annotations

import json
import re
import uuid
from html.parser import HTMLParser
from typing import NamedTuple
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse, urlunparse, urlencode
from xml.etree import ElementTree

import httpx

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion.fetcher import TIMEOUT_SECONDS, fetch_url, robots_allowed, user_agent
from backend.progress import ProgressEvent, emit_progress

_BINARY_EXTENSIONS = frozenset(
    [
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z",
        ".mp4", ".mp3", ".mov", ".avi", ".wmv", ".webm",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".dmg", ".exe", ".pkg", ".deb", ".rpm",
        ".css", ".js",
    ]
)

# Unambiguous tracking-only query params. Keep this list narrow — ambiguous
# names like "ref" or "source" are sometimes real page keys.
_TRACKING_PARAMS = frozenset(
    [
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "yclid",
        "mc_cid", "mc_eid", "_ga",
    ]
)


async def run_sitemap(
    source_run_id: uuid.UUID | str,
    source_input: str,
    *,
    store: Store,
) -> dict[str, int]:
    run_id = uuid.UUID(str(source_run_id))
    root_host = _root_host(source_input)
    if not root_host:
        store.update_source_run(
            run_id,
            status="failed",
            stats={"errors": 1},
            error_message=f"invalid source input: {source_input!r}",
            finished=True,
        )
        await emit_progress(
            run_id, ProgressEvent("crawl", "failed", "invalid source input", 100.0)
        )
        return {"sitemaps": 0, "discovered": 0, "fetched": 0, "errors": 1}

    stats: dict[str, int] = {
        "sitemaps": 0,
        "discovered": 0,
        "fetched": 0,
        "errors": 0,
        "from_sitemap": 0,
        "from_link_follow": 0,
        "dropped_scope": 0,
        "dropped_filter": 0,
    }

    await emit_progress(run_id, ProgressEvent("crawl", "running", "Starting crawl", 0.0))

    queue: list[tuple[str, str]] = []  # (url, discovery_method)
    seen: set[str] = set()
    cap = get_settings().max_pages

    # Step 1: gather seed URLs from sitemap (if input looks like a sitemap) or from host root.
    seed_urls: list[str] = []
    if _looks_like_sitemap(source_input):
        try:
            seed_urls = await _collect_from_sitemap(source_input, stats, seen=set())
        except Exception:  # noqa: BLE001 - failures recorded on run.
            stats["errors"] += 1
    else:
        # Bare host: start at the root.
        root_url = f"https://{root_host}/"
        seed_urls = [root_url]

    for url in seed_urls:
        _enqueue(url, "sitemap" if _looks_like_sitemap(source_input) else "seed",
                 queue=queue, seen=seen, root_host=root_host, stats=stats)

    # Step 2: BFS retrieval loop.
    processed = 0
    while queue and stats["fetched"] < cap:
        url, method = queue.pop(0)
        try:
            result = await _retrieve(url)
        except PermissionError as exc:
            stats["errors"] += 1
            store.add_fetch(
                source_run_id=run_id,
                url=url,
                body_bytes=None,
                error_message=str(exc),
                original_url=url,
                discovery_method=method,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - record per URL.
            stats["errors"] += 1
            store.add_fetch(
                source_run_id=run_id,
                url=url,
                body_bytes=None,
                error_message=f"{type(exc).__name__}: {exc}",
                original_url=url,
                discovery_method=method,
            )
            continue

        final_url = result.final_url
        # Apply scope to the final URL after redirects.
        if not _in_scope(final_url, root_host):
            stats["dropped_scope"] += 1
            continue

        stats["fetched"] += 1
        if method == "sitemap":
            stats["from_sitemap"] += 1
        elif method == "link_follow":
            stats["from_link_follow"] += 1
        store.add_fetch(
            source_run_id=run_id,
            url=final_url,
            body_bytes=result.body,
            http_status=result.status,
            content_type=result.content_type,
            original_url=url,
            redirect_chain=result.chain if len(result.chain) > 1 else None,
            discovery_method=method,
        )

        # Discover new URLs from HTML body.
        if result.is_html:
            for discovered in _discover_links(result.body, base_url=final_url):
                _enqueue(discovered, "link_follow",
                         queue=queue, seen=seen, root_host=root_host, stats=stats)

        processed += 1
        if processed % 5 == 0 or not queue:
            percent = min(99.0, (stats["fetched"] / cap) * 100.0)
            await emit_progress(
                run_id, ProgressEvent("crawl", "running", "Retrieving documents", percent)
            )

    status = "complete" if stats["errors"] == 0 else "partial"
    if stats["fetched"] == 0 and stats["errors"] > 0:
        status = "failed"
    store.update_source_run(run_id, status=status, stats=stats, finished=True)
    await emit_progress(
        run_id,
        ProgressEvent("crawl", "failed" if status == "failed" else "complete", status, 100.0),
    )
    return stats


# ───── Retrieval ─────


class _Result(NamedTuple):
    body: bytes
    status: int
    content_type: str | None
    final_url: str
    chain: list[str]
    is_html: bool


async def _retrieve(url: str) -> _Result:
    """Retrieve a URL, capturing redirect chain and content type. Honors robots.txt via fetch_url for the
    initial URL; httpx handles HTTP-level redirect resolution up to 20 hops (Chrome default)."""
    if not await robots_allowed(url):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    headers = {"User-Agent": user_agent()}
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, headers=headers, max_redirects=20
    ) as client:
        response = await client.get(url, follow_redirects=True)

    chain = [str(r.url) for r in response.history] + [str(response.url)]
    final_url = str(response.url)
    content_type = response.headers.get("content-type")
    is_html = bool(content_type and "html" in content_type.lower())
    return _Result(
        body=response.content,
        status=response.status_code,
        content_type=content_type,
        final_url=final_url,
        chain=chain,
        is_html=is_html,
    )


# ───── Sitemap parsing ─────


def _looks_like_sitemap(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return False
    return parsed.path.lower().endswith(".xml") or "sitemap" in parsed.path.lower()


async def _collect_from_sitemap(
    sitemap_url: str,
    stats: dict[str, int],
    *,
    seen: set[str],
) -> list[str]:
    if sitemap_url in seen:
        return []
    seen.add(sitemap_url)
    stats["sitemaps"] += 1
    raw = await fetch_url(sitemap_url)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return []
    root_name = _local_name(root.tag)
    locs = [t.strip() for t in _loc_texts(root) if t and t.strip()]
    if root_name == "sitemapindex":
        urls: list[str] = []
        for loc in locs:
            urls.extend(await _collect_from_sitemap(loc, stats, seen=seen))
        return urls
    if root_name != "urlset":
        return []
    stats["discovered"] += len(locs)
    return locs


def _loc_texts(root: ElementTree.Element) -> list[str]:
    return [
        element.text for element in root.iter()
        if _local_name(element.tag) == "loc" and element.text
    ]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ───── HTML link discovery ─────


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base: str | None = None
        self._in_jsonld = False
        self._jsonld_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "base":
            href = attr.get("href")
            if href and self.base is None:
                self.base = href
            return
        if tag == "a":
            href = attr.get("href")
            if href:
                self.links.append(href)
        elif tag == "link":
            rel = (attr.get("rel") or "").lower()
            if any(r in rel for r in ("canonical", "alternate", "next", "prev", "sitemap")):
                href = attr.get("href")
                if href:
                    self.links.append(href)
        elif tag in {"iframe", "frame"}:
            src = attr.get("src")
            if src:
                self.links.append(src)
        elif tag == "area":
            href = attr.get("href")
            if href:
                self.links.append(href)
        elif tag == "meta":
            http_equiv = (attr.get("http-equiv") or "").lower()
            if http_equiv == "refresh":
                content = attr.get("content") or ""
                # content format: "5; url=https://example.com/"
                match = re.search(r"url\s*=\s*([^\s;'\"]+)", content, re.IGNORECASE)
                if match:
                    self.links.append(match.group(1))
        elif tag == "script":
            if (attr.get("type") or "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            try:
                payload = json.loads("".join(self._jsonld_buffer))
            except json.JSONDecodeError:
                return
            self.links.extend(_jsonld_urls(payload))


def _jsonld_urls(node: object) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key in ("url", "@id", "mainEntityOfPage", "sameAs"):
            value = node.get(key)
            if isinstance(value, str):
                found.append(value)
            elif isinstance(value, list):
                found.extend(v for v in value if isinstance(v, str))
            elif isinstance(value, dict):
                found.extend(_jsonld_urls(value))
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_jsonld_urls(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_jsonld_urls(item))
    return found


def _discover_links(body: bytes, *, base_url: str) -> list[str]:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return []
    parser = _LinkCollector()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 - malformed HTML.
        pass
    effective_base = urljoin(base_url, parser.base) if parser.base else base_url
    resolved: list[str] = []
    for href in parser.links:
        href = href.strip()
        if not href:
            continue
        resolved.append(urljoin(effective_base, href))
    return resolved


# ───── Normalization / scope / filters ─────


def _root_host(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.netloc
    else:
        host = value.split("/", 1)[0]
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return host


def _in_scope(url: str, root_host: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host == root_host or host.endswith("." + root_host)


def _passes_filters(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    for ext in _BINARY_EXTENSIONS:
        if path.endswith(ext):
            return False
    return True


def _canonicalize(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    # Strip default ports.
    if (scheme == "http" and host.endswith(":80")) or (scheme == "https" and host.endswith(":443")):
        host = host.rsplit(":", 1)[0]
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # Sort query, drop tracking params.
    query_pairs = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    # Drop fragment.
    cleaned = urlunparse((scheme, host, path, parsed.params, query, ""))
    cleaned, _ = urldefrag(cleaned)
    return cleaned


def _enqueue(
    url: str,
    method: str,
    *,
    queue: list[tuple[str, str]],
    seen: set[str],
    root_host: str,
    stats: dict[str, int],
) -> None:
    url = url.strip()
    if not url:
        return
    canonical = _canonicalize(url)
    if canonical in seen:
        return
    if not _in_scope(canonical, root_host):
        stats["dropped_scope"] += 1
        seen.add(canonical)
        return
    if not _passes_filters(canonical):
        stats["dropped_filter"] += 1
        seen.add(canonical)
        return
    seen.add(canonical)
    queue.append((canonical, method))
