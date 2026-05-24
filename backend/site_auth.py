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

BYPASS_PATHS = {"/health", "/favicon.svg"}
WWW_AUTHENTICATE = 'Basic realm="Pinegraf"'


class SiteAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        if request.url.path in BYPASS_PATHS or request.url.path.startswith("/admin"):
            return await call_next(request)

        settings = get_settings()
        expected_password = settings.site_auth_password
        if not expected_password:
            return PlainTextResponse("site auth is not configured", status_code=503)

        credentials = _basic_credentials(request.headers.get("authorization"))
        if credentials is None:
            return _auth_required_response()

        username, password = credentials
        user_ok = secrets.compare_digest(username, settings.site_auth_user)
        password_ok = secrets.compare_digest(password, expected_password)
        if not (user_ok and password_ok):
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
