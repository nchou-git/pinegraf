from __future__ import annotations

from urllib.parse import urlparse


def normalize_identifier(kind: str, raw: str) -> str:
    value = str(raw or "").strip()
    if kind == "file":
        return value
    if kind != "domain":
        return value

    if "://" not in value:
        value = f"//{value}"
    parsed = urlparse(value)
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")
