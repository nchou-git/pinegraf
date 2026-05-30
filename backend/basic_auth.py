"""Optional HTTP Basic Auth wall in front of the entire app.

Enabled only when BASIC_AUTH_CREDENTIALS env var is set, format
"username:password". This is a separate layer from the app's own admin
auth -- it's a perimeter to keep search engines and casual visitors out
of pre-public environments.
"""

from __future__ import annotations

import base64
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

EXEMPT_PATHS = frozenset({"/health", "/robots.txt"})


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, credentials: str) -> None:
        super().__init__(app)
        self._expected = "Basic " + base64.b64encode(credentials.encode()).decode()

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if header != self._expected:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="pinegrafdemo"'},
            )
        return await call_next(request)


def install_basic_auth(app) -> None:
    credentials = os.getenv("BASIC_AUTH_CREDENTIALS", "")
    if credentials and ":" in credentials:
        app.add_middleware(BasicAuthMiddleware, credentials=credentials)
