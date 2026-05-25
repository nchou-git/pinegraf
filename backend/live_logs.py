from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime

LogLine = dict[str, str]

_HISTORY: deque[LogLine] = deque(maxlen=200)
_SUBSCRIBERS: list[asyncio.Queue[LogLine]] = []


class LiveLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        append_log(record.levelname, record.getMessage())


def install_log_handler() -> None:
    root = logging.getLogger()
    if any(isinstance(handler, LiveLogHandler) for handler in root.handlers):
        return
    handler = LiveLogHandler()
    handler.setLevel(logging.INFO)
    root.addHandler(handler)


def append_log(level: str, message: str) -> None:
    line = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level.upper(),
        "message": message,
    }
    _HISTORY.append(line)
    for queue in list(_SUBSCRIBERS):
        queue.put_nowait(line)


async def subscribe_logs() -> AsyncIterator[LogLine]:
    queue: asyncio.Queue[LogLine] = asyncio.Queue()
    for line in _HISTORY:
        await queue.put(line)
    _SUBSCRIBERS.append(queue)
    try:
        while True:
            yield await queue.get()
    finally:
        _SUBSCRIBERS.remove(queue)
