from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from hashlib import sha256
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from backend.config import get_settings
from backend.db.models import AuditEvent
from backend.db.store import Store

ADMIN_COOKIE_NAME = "pinegraf_admin"
STATIC_GET_PATHS = {"/app.js", "/admin.js", "/favicon.svg", "/"}
AUDITED_EXACT_PATHS = {"/lookup", "/research"}
REDACTED = "[redacted]"


class AdminLoginRequest(BaseModel):
    password: str


class AuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, *, store: Store) -> None:
        super().__init__(app)
        self.store = store

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        should_log = _should_audit(request)
        body = b""
        if should_log:
            body = await request.body()
            request._body = body  # noqa: SLF001 - Starlette caches body here for downstream use.
            self.store.add_audit_event(
                actor=actor_for_request(request),
                action=action_for_path(request.url.path),
                payload={
                    "method": request.method,
                    "path": request.url.path,
                    "body": _parse_body(body),
                },
            )
        return await call_next(request)


def install_audit_middleware(app: FastAPI, store: Store) -> None:
    app.add_middleware(AuditMiddleware, store=store)


def actor_for_request(request: Request) -> str:
    return "admin" if is_admin_request(request) else "anon"


def is_admin_request(request: Request) -> bool:
    cookie = request.cookies.get(ADMIN_COOKIE_NAME, "")
    expected = admin_cookie_value()
    return bool(cookie) and hmac.compare_digest(cookie, expected)


def admin_cookie_value() -> str:
    settings = get_settings()
    payload = f"{settings.pinegraf_admin_password}:{settings.pinegraf_admin_cookie_secret}"
    return hmac.new(
        settings.pinegraf_admin_cookie_secret.encode("utf-8"),
        payload.encode("utf-8"),
        sha256,
    ).hexdigest()


def login_admin(payload: AdminLoginRequest, response: Response) -> dict[str, str]:
    settings = get_settings()
    if not hmac.compare_digest(payload.password, settings.pinegraf_admin_password):
        raise HTTPException(status_code=403, detail="Invalid admin password")
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        admin_cookie_value(),
        httponly=True,
        samesite="lax",
    )
    return {"status": "ok"}


def audit_events_response(
    *,
    store: Store,
    request: Request,
    since: datetime | None,
    until: datetime | None,
    actor: str | None,
    action: str | None,
    limit: int,
    before_id: int | None,
) -> dict[str, object]:
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin authentication required")
    capped_limit = min(max(limit, 1), 1000)
    events = store.list_audit_events(
        since=since,
        until=until,
        actor=actor,
        action=action,
        limit=capped_limit,
        before_id=before_id,
    )
    next_before_id = events[-1].id if len(events) == capped_limit else None
    return {
        "events": [audit_event_to_dict(event) for event in events],
        "next_before_id": next_before_id,
    }


def audit_event_to_dict(event: AuditEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "actor": event.actor,
        "action": event.action,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def action_for_path(path: str) -> str:
    if path == "/lookup":
        return "lookup"
    if path == "/research":
        return "research"
    if path.startswith("/admin/"):
        suffix = path.removeprefix("/admin/").split("/", 1)[0]
        return f"admin_{suffix.replace('-', '_')}"
    return "request"


def _should_audit(request: Request) -> bool:
    path = request.url.path
    if request.method == "GET" and path in STATIC_GET_PATHS:
        return False
    return path in AUDITED_EXACT_PATHS or path.startswith("/admin/")


def _parse_body(body: bytes) -> object:
    if not body:
        return None
    try:
        loaded = json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace")
    return _redact(loaded)


def _redact(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            if key.lower() in {"password", "token", "api_key"}:
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(nested)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
