from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ProgressEvent:
    stage: str
    status: str
    message: str
    percent: float
    data: dict[str, object] = field(default_factory=dict)


_SUBSCRIBERS: dict[uuid.UUID, list[asyncio.Queue[ProgressEvent]]] = defaultdict(list)
_LATEST: dict[uuid.UUID, ProgressEvent] = {}


def has_progress(run_id: uuid.UUID | str) -> bool:
    return uuid.UUID(str(run_id)) in _LATEST


async def subscribe(run_id: uuid.UUID | str) -> AsyncIterator[ProgressEvent]:
    run_uuid = uuid.UUID(str(run_id))
    queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
    latest = _LATEST.get(run_uuid)
    if latest is not None:
        await queue.put(latest)
    _SUBSCRIBERS[run_uuid].append(queue)
    try:
        while True:
            event = await queue.get()
            yield event
            if event.status in {"complete", "failed"}:
                break
    finally:
        _SUBSCRIBERS[run_uuid].remove(queue)


async def emit_progress(run_id: uuid.UUID | str, event: ProgressEvent) -> None:
    run_uuid = uuid.UUID(str(run_id))
    event.percent = round(max(0.0, min(100.0, float(event.percent))), 1)
    _LATEST[run_uuid] = event
    for queue in list(_SUBSCRIBERS.get(run_uuid, [])):
        await queue.put(event)
