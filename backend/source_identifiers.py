from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def _looks_like_sitemap(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(".xml") or "sitemap" in lowered


def normalize_identifier(kind: str, raw: str) -> str:
    value = str(raw or "").strip()
    if kind == "file":
        return value
    if kind != "domain":
        return value

    candidate = value if "://" in value else f"//{value}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    host = host.rstrip("/")

    # A sitemap URL keeps its path so the crawler ingests the listed pages
    # instead of crawling the whole domain; every other website input collapses
    # to a bare host so each site stays a single, de-duplicated source. Query
    # strings and fragments are dropped to keep identifiers canonical.
    path = parsed.path if parsed.netloc else ""
    if host and _looks_like_sitemap(path):
        return urlunparse(("https", host, path, "", "", ""))

    return host
