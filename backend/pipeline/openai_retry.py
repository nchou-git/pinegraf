from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeVar

import httpx
import openai

T = TypeVar("T")

OPENAI_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def is_insufficient_quota_error(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code == "insufficient_quota":
        return True

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error_body = body.get("error", body)
        if isinstance(error_body, dict):
            if error_body.get("code") == "insufficient_quota":
                return True
            if error_body.get("type") == "insufficient_quota":
                return True

    message = str(exc).lower()
    return "insufficient_quota" in message or "insufficient quota" in message


def is_retryable_openai_error(exc: BaseException) -> bool:
    if is_insufficient_quota_error(exc):
        return False
    if isinstance(exc, (openai.BadRequestError, openai.AuthenticationError)):
        return False
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, openai.RateLimitError):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500
    return False


def retry_openai_call(
    call: Callable[[], T],
    *,
    backoff_seconds: Sequence[float] = OPENAI_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    for attempt in range(len(backoff_seconds) + 1):
        try:
            return call()
        except Exception as exc:
            if attempt >= len(backoff_seconds) or not is_retryable_openai_error(exc):
                raise
            sleep(backoff_seconds[attempt])
    raise RuntimeError("unreachable OpenAI retry state")
