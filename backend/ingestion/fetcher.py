from __future__ import annotations

import asyncio
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

from backend.config import get_settings

RETRY_COUNT = 2
TIMEOUT_SECONDS = 30.0
_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


def user_agent() -> str:
    return f"Pinegraf/1.0 (knowledge graph; contact: {get_settings().pinegraf_contact})"


async def fetch_url(url: str) -> bytes:
    if not await robots_allowed(url):
        raise PermissionError(f"robots.txt disallows fetching {url}")

    headers = {"User-Agent": user_agent()}
    last_error: Exception | None = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
                response = await client.get(url, follow_redirects=True)
            if response.status_code >= 500:
                if attempt < RETRY_COUNT:
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
            if 400 <= response.status_code < 500 and response.status_code != 404:
                response.raise_for_status()
            return response.content
        except httpx.TransportError as exc:
            last_error = exc
            if attempt < RETRY_COUNT:
                await asyncio.sleep(2**attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"failed to fetch {url}")


async def robots_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    cache_key = f"{parsed.scheme}://{parsed.netloc}"
    parser = _ROBOTS_CACHE.get(cache_key)
    if cache_key not in _ROBOTS_CACHE:
        parser = await _load_robots(cache_key)
        _ROBOTS_CACHE[cache_key] = parser
    if parser is None:
        return True
    return parser.can_fetch(user_agent(), url)


async def _load_robots(origin: str) -> RobotFileParser | None:
    parsed = urlparse(origin)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            headers={"User-Agent": user_agent()},
        ) as client:
            response = await client.get(robots_url, follow_redirects=True)
    except httpx.TransportError:
        return None
    if response.status_code >= 400:
        return None
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.text.splitlines())
    return parser
