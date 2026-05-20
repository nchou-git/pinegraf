from __future__ import annotations

import calendar
from datetime import date


def parse_position_date(value: str | None, *, is_end_date: bool) -> date | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None

    parts = cleaned.split("-")
    if len(parts) == 1:
        year = _parse_part(parts[0], 1, 9999)
        if year is None:
            return None
        month = 12 if is_end_date else 1
        day = 31 if is_end_date else 1
        return date(year, month, day)
    if len(parts) == 2:
        year = _parse_part(parts[0], 1, 9999)
        month = _parse_part(parts[1], 1, 12)
        if year is None or month is None:
            return None
        day = calendar.monthrange(year, month)[1] if is_end_date else 1
        return date(year, month, day)
    if len(parts) == 3:
        year = _parse_part(parts[0], 1, 9999)
        month = _parse_part(parts[1], 1, 12)
        day = _parse_part(parts[2], 1, 31)
        if year is None or month is None or day is None:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _parse_part(value: str, min_value: int, max_value: int) -> int | None:
    if not value.isdigit():
        return None
    parsed = int(value)
    if parsed < min_value or parsed > max_value:
        return None
    return parsed
