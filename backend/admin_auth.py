from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from backend.admin_session import COOKIE_NAME, verify
from backend.config import get_settings

ADMIN_USERNAME = "pinegraf"


def require_admin(request: Request) -> None:
    if is_admin_request(request):
        return
    raise _unauthorized()


def is_admin_request(request: Request) -> bool:
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value and verify(cookie_value) is not None:
        return True
    return False


def valid_admin_credentials(username: str, password: str) -> bool:
    expected = get_settings().pinegraf_admin_password
    if not expected:
        return False
    return secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(
        password, expected
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="admin auth required",
    )
