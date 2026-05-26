from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import delete, select

from backend.db.models import LiveLog
from backend.db.store import Store, utc_now

LogLine = dict[str, str | None]
LOG_CAP = 2000
INITIAL_REPLAY_LIMIT = 200

_DEFAULT_STORE: Store | None = None


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


def append_log(
    level: str,
    message: str,
    *,
    source_run_id: uuid.UUID | str | None = None,
    store: Store | None = None,
) -> None:
    try:
        db = _store(store)
        with db.session() as session:
            row = LiveLog(
                timestamp=utc_now(),
                level=level.upper(),
                message=message,
                source_run_id=uuid.UUID(str(source_run_id)) if source_run_id else None,
            )
            session.add(row)
            session.flush()
            latest_ids = (
                select(LiveLog.id)
                .order_by(LiveLog.timestamp.desc(), LiveLog.id.desc())
                .limit(LOG_CAP)
                .subquery()
            )
            session.execute(delete(LiveLog).where(LiveLog.id.not_in(select(latest_ids.c.id))))
            session.commit()
    except Exception:  # noqa: BLE001
        pass


async def subscribe_logs(
    *,
    store: Store | None = None,
    poll_seconds: float = 0.5,
) -> AsyncIterator[LogLine]:
    db = _store(store)
    seen: set[uuid.UUID] = set()
    last_timestamp: datetime | None = None
    for line, row_id, timestamp in _read_logs(db, since=None, limit=INITIAL_REPLAY_LIMIT):
        seen.add(row_id)
        last_timestamp = timestamp
        yield line

    while True:
        emitted = False
        for line, row_id, timestamp in _read_logs(db, since=last_timestamp):
            if row_id in seen:
                continue
            seen.add(row_id)
            last_timestamp = timestamp
            emitted = True
            yield line
        if len(seen) > LOG_CAP:
            seen = set(list(seen)[-LOG_CAP:])
        if not emitted:
            await asyncio.sleep(poll_seconds)


def _read_logs(
    store: Store,
    *,
    since: datetime | None,
    limit: int | None = None,
) -> list[tuple[LogLine, uuid.UUID, datetime]]:
    with store.session() as session:
        if since is None:
            stmt = select(LiveLog).order_by(LiveLog.timestamp.desc(), LiveLog.id.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            rows = list(session.execute(stmt).scalars())
            rows.reverse()
        else:
            rows = list(
                session.execute(
                    select(LiveLog)
                    .where(LiveLog.timestamp >= since)
                    .order_by(LiveLog.timestamp.asc(), LiveLog.id.asc())
                ).scalars()
            )
        return [(_to_line(row), row.id, row.timestamp) for row in rows]


def _to_line(row: LiveLog) -> LogLine:
    return {
        "timestamp": row.timestamp.isoformat(),
        "level": row.level,
        "message": row.message,
        "source_run_id": str(row.source_run_id) if row.source_run_id else None,
    }


def _store(store: Store | None) -> Store:
    global _DEFAULT_STORE
    if store is not None:
        return store
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = Store()
    return _DEFAULT_STORE
