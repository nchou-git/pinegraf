"""Optional HTTP Basic Auth wall in front of the entire app.

Enabled only when BASIC_AUTH_CREDENTIALS env var is set, format
"username:password". This is a separate layer from the app's own admin
auth -- it's a perimeter to keep search engines and casual visitors out
of pre-public environments.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from backend.config import get_settings

COOKIE_NAME = "demo_session"
SALT = "pinegraf.demo.session.v1"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
EXEMPT_PATHS = frozenset({"/health", "/robots.txt", "/demo-login", "/favicon.svg", "/styles.css"})
EXEMPT_PREFIXES = ("/styles/", "/assets/")


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.admin_session_secret, salt=SALT)


def issue_demo_session(user: str) -> str:
    payload = {"u": user, "iat": int(time.time())}
    return _serializer().dumps(payload)


def verify_demo_session(token: str | None) -> dict[str, object] | None:
    if not token:
        return None
    try:
        settings = get_settings()
        data = _serializer().loads(token, max_age=settings.admin_session_max_age_seconds)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    return data


def valid_basic_credentials(username: str, password: str) -> bool:
    expected = os.getenv("BASIC_AUTH_CREDENTIALS", "")
    if ":" not in expected:
        return False
    expected_username, expected_password = expected.split(":", 1)
    return secrets.compare_digest(username, expected_username) and secrets.compare_digest(
        password, expected_password
    )


def _is_exempt_path(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def _wants_json(request: Request) -> bool:
    path = request.url.path
    accept = request.headers.get("accept", "")
    return path.startswith("/api/") or path.startswith("/admin/") or "application/json" in accept


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _has_basic_authorization(request: Request) -> bool:
    return request.headers.get("authorization", "").startswith("Basic ")


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, credentials: str) -> None:
        super().__init__(app)
        self._expected = "Basic " + base64.b64encode(credentials.encode()).decode()

    async def dispatch(self, request: Request, call_next):
        if _is_exempt_path(request.url.path):
            return await call_next(request)
        if verify_demo_session(request.cookies.get(COOKIE_NAME)) is not None:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header == self._expected:
            return await call_next(request)
        if _wants_json(request):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                media_type="application/json",
            )
        if _wants_html(request):
            return HTMLResponse(
                (FRONTEND_DIR / "login.html").read_text(encoding="utf-8"),
                status_code=200,
                media_type="text/html",
            )
        headers = {"WWW-Authenticate": "Basic"} if _has_basic_authorization(request) else None
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers=headers,
            media_type="application/json",
        )


def install_basic_auth(app) -> None:
    credentials = os.getenv("BASIC_AUTH_CREDENTIALS", "")
    if credentials and ":" in credentials:
        app.add_middleware(BasicAuthMiddleware, credentials=credentials)
