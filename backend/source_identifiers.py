from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def _looks_like_sitemap(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(".xml") or "sitemap" in lowered


def normalize_identifier(kind: str, raw: str, *, crawl_depth: int | None = None) -> str:
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

    path = parsed.path if parsed.netloc else ""

    # Website / page mode (bounded depth): keep the exact URL so distinct pages
    # on one domain stay distinct sources. Always carries a scheme + path, so a
    # depth-limited homepage source won't collide with a full-crawl source.
    if crawl_depth is not None:
        if not host:
            return ""
        return urlunparse(("https", host, path or "/", "", "", ""))

    # Full-crawl (Sitemap) mode: keep a sitemap URL, else collapse to the bare
    # host (one source per domain). Query/fragment dropped.
    if host and _looks_like_sitemap(path):
        return urlunparse(("https", host, path, "", "", ""))
    return host
