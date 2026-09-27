"""Calendar dates in the user's selected IANA time zone."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_time_zone(value: str) -> str:
    """Accept IANA zones reported by browsers as well as the settings shortlist."""
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("Unknown time zone") from exc
    return value


def local_today(time_zone: str) -> date:
    return datetime.now(ZoneInfo(time_zone)).date()
