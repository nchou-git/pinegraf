from __future__ import annotations

import secrets

from fastapi import Request

from backend.config import get_settings

ADMIN_USERNAME = "pinegraf"


def require_admin(request: Request) -> None:
    del request
    return


def is_admin_request(request: Request) -> bool:
    del request
    return True


def valid_admin_credentials(username: str, password: str) -> bool:
    expected = get_settings().pinegraf_admin_password
    if not expected:
        return False
    return secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(
        password, expected
    )
