from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select

from backend.config import get_settings
from backend.db.models import Source, SourceRun

if TYPE_CHECKING:
    from backend.db.store import Store

RETRY_COUNT = 2
TIMEOUT_SECONDS = 30.0
_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}
_ROBOTS_CACHE_LOCK = asyncio.Lock()
_ROBOTS_LOAD_LOCKS: dict[str, asyncio.Lock] = {}


def user_agent() -> str:
    return f"Pinegraf/1.0 (knowledge graph; contact: {get_settings().pinegraf_contact})"


async def fetch_url(
    url: str,
    *,
    store: "Store | None" = None,
    source_run_id: uuid.UUID | str | None = None,
) -> bytes:
    if not await robots_allowed(url, store=store, source_run_id=source_run_id):
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


async def robots_allowed(
    url: str,
    *,
    store: "Store | None" = None,
    source_run_id: uuid.UUID | str | None = None,
) -> bool:
    if store is not None and source_run_id is not None:
        if not _source_respects_robots(store, source_run_id):
            return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    cache_key = f"{parsed.scheme}://{parsed.netloc}"
    async with _ROBOTS_CACHE_LOCK:
        if cache_key in _ROBOTS_CACHE:
            parser = _ROBOTS_CACHE[cache_key]
            needs_load = False
        else:
            parser = None
            needs_load = True
            load_lock = _ROBOTS_LOAD_LOCKS.setdefault(cache_key, asyncio.Lock())
    if needs_load:
        async with load_lock:
            async with _ROBOTS_CACHE_LOCK:
                if cache_key in _ROBOTS_CACHE:
                    parser = _ROBOTS_CACHE[cache_key]
                    needs_load = False
                else:
                    parser = None
                    needs_load = True
            if needs_load:
                parser = await _load_robots(cache_key)
                async with _ROBOTS_CACHE_LOCK:
                    _ROBOTS_CACHE[cache_key] = parser
                    _ROBOTS_LOAD_LOCKS.pop(cache_key, None)
    if parser is None:
        return True
    return parser.can_fetch(user_agent(), url)


def _source_respects_robots(store: "Store", source_run_id: uuid.UUID | str) -> bool:
    run_id = uuid.UUID(str(source_run_id))
    with store.session() as session:
        value = session.execute(
            select(Source.respect_robots)
            .join(SourceRun, SourceRun.source_id == Source.id)
            .where(SourceRun.id == run_id)
        ).scalar_one_or_none()
    return True if value is None else bool(value)


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
