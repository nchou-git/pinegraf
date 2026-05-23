from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from backend.config import get_settings
from backend.site_auth import SiteAuthMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SiteAuthMiddleware)

    @app.get("/protected")
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _get(path: str, *, auth: tuple[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        auth=auth,
    ) as client:
        return await client.get(path)


def test_site_auth_requires_basic_header(monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()

    response = asyncio.run(_get("/protected"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Pinegraf"'


def test_site_auth_rejects_wrong_password(monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()

    response = asyncio.run(_get("/protected", auth=("pinegraf", "wrong")))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Pinegraf"'


def test_site_auth_accepts_correct_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()

    response = asyncio.run(_get("/protected", auth=("pinegraf", "secret")))

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_site_auth_bypasses_health(monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "secret")
    get_settings.cache_clear()

    response = asyncio.run(_get("/health"))

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_site_auth_fails_closed_without_password(monkeypatch) -> None:
    monkeypatch.setenv("SITE_AUTH_USER", "pinegraf")
    monkeypatch.delenv("SITE_AUTH_PASSWORD", raising=False)
    get_settings.cache_clear()

    response = asyncio.run(_get("/protected", auth=("pinegraf", "secret")))

    assert response.status_code == 503
