from __future__ import annotations

import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.config import get_settings

COOKIE_NAME = "pg_admin"
SALT = "pinegraf.admin.session.v1"


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.admin_session_secret, salt=SALT)


def issue(user: str = "pinegraf") -> str:
    payload = {"u": user, "iat": int(time.time())}
    return _serializer().dumps(payload)


def verify(token: str | None) -> dict[str, object] | None:
    if not token:
        return None
    try:
        settings = get_settings()
        max_age = settings.admin_session_max_age_seconds
        if settings.demo_mode:
            max_age = max(max_age, 60 * 60 * 24 * 7)
        data = _serializer().loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    return data
