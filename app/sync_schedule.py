"""Clock schedules use account-local dates and UTC instants for comparisons."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .sync_runtime import parse_runtime_datetime


def local_slot(day, clock, zone):
    # The first occurrence is used on a repeated hour. In a spring gap,
    # the UTC conversion moves the slot forward by the clock change.
    return datetime.combine(day, time.fromisoformat(clock), zone).astimezone(timezone.utc)


def next_clock_sync(now, zone_name, clock, runtime, *, weekday=None):
    zone = ZoneInfo(zone_name)
    today = now.astimezone(zone).date()
    attempted = parse_runtime_datetime(runtime.get("last_scheduled_slot"))
    for offset in range(8):
        day = today + timedelta(days=offset)
        if weekday is not None and day.weekday() != weekday:
            continue
        slot = local_slot(day, clock, zone)
        if attempted is not None and attempted >= slot:
            continue
        return slot
    return None


def next_digest_at(now, zone_name, clock, *, weekday=None):
    zone = ZoneInfo(zone_name)
    day = now.astimezone(zone).date()
    if weekday is not None:
        day += timedelta(days=(weekday - day.weekday()) % 7)
    target = local_slot(day, clock, zone)
    return target if target > now else local_slot(day + timedelta(days=7 if weekday is not None else 1), clock, zone)
