from __future__ import annotations

import base64
import binascii
import secrets

from fastapi import HTTPException, Request

from backend.config import get_settings

WWW_AUTHENTICATE = 'Basic realm="Pinegraf Admin"'


def require_admin(request: Request) -> None:
    if is_admin_request(request):
        return
    raise _unauthorized()


def is_admin_request(request: Request) -> bool:
    credentials = _basic_credentials(request.headers.get("authorization"))
    if credentials is None:
        return False
    _username, password = credentials
    expected = get_settings().pinegraf_admin_password
    return secrets.compare_digest(password, expected)


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


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="admin auth required",
        headers={"WWW-Authenticate": WWW_AUTHENTICATE},
    )
