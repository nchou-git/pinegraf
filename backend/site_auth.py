from __future__ import annotations

import base64
import binascii
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.responses import Response as StarletteResponse

from backend.config import get_settings

BYPASS_PATHS = {
    "/health",
    "/app.js",
    "/styles.css",
    "/favicon.svg",
    "/admin/login",
    "/admin/logout",
}
WWW_AUTHENTICATE = 'Basic realm="Pinegraf"'


class SiteAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        path = request.url.path
        if path in BYPASS_PATHS or path.startswith("/admin/"):
            return await call_next(request)

        settings = get_settings()
        expected_password = settings.site_auth_password
        if not expected_password:
            return PlainTextResponse("site auth is not configured", status_code=503)

        credentials = _basic_credentials(request.headers.get("authorization"))
        if credentials is None:
            return _auth_required_response()

        username, password = credentials
        site_user_ok = secrets.compare_digest(username, settings.site_auth_user)
        site_password_ok = secrets.compare_digest(password, expected_password)
        admin_password_ok = bool(settings.pinegraf_admin_password) and secrets.compare_digest(
            password, settings.pinegraf_admin_password
        )
        if not ((site_user_ok and site_password_ok) or admin_password_ok):
            return _auth_required_response()

        return await call_next(request)


def _basic_credentials(header_value: str | None) -> tuple[str, str] | None:
    if not header_value:
        return None
    scheme, _, encoded = header_value.partition(" ")
    if scheme.casefold() != "basic" or not encoded.strip():
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def _auth_required_response() -> PlainTextResponse:
    return PlainTextResponse(
        "authentication required",
        status_code=401,
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )
