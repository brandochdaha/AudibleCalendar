"""Deliver release notifications on their own schedule or after scheduled syncs."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from starlette.concurrency import run_in_threadpool

from .notification_delivery import DeliveryError, deliver
from .notification_settings import CHANNELS
from .notification_store import claim_due, finish_delivery, load_preferences, queue_releases
from .repository import calendar
from .repository.presentation import summaries
from .sync_runtime import parse_runtime_datetime, read_sync_runtime
from .sync_schedule import local_slot


logger = logging.getLogger("audiblecalendar")
CATCH_UP_DAYS = 7


def digest_slot(settings, completions, local, now):
    if settings.after_sync:
        for event in reversed(completions):
            completed = parse_runtime_datetime(event["at"])
            if completed is not None and completed + timedelta(minutes=settings.delay_minutes) <= now:
                day = completed.astimezone(local.tzinfo).date().isoformat()
                return day, f"{day}/{completed.isoformat()}", event["status"] != "ok"
        return None
    day = local.date()
    if settings.cadence == "weekly":
        day -= timedelta(days=(day.weekday() - settings.weekday) % 7)
        if now < local_slot(day, settings.time, local.tzinfo):
            day -= timedelta(days=7)
    elif now < local_slot(day, settings.time, local.tzinfo):
        return None
    return day.isoformat(), day.isoformat(), False


def release_snapshot(book):
    return {
        "asin": book["asin"],
        "title": book["title"],
        "release_date": book["release_date"],
        "authors": [author["name"] for author in book["authors"] if author.get("name")],
        "series": [series["name"] for series in book["series"] if series.get("name")],
        "image_url": book.get("image_url"),
        "audible_url": book.get("audible_url"),
        "presumed": book.get("is_available") != 1 or bool(book.get("is_preorder")),
    }


def prepare_deliveries(user_store, workspace, now):
    user_id = workspace.user.id
    region = workspace.settings.region
    local = now.astimezone(ZoneInfo(workspace.settings.time_zone))
    day = local.date()
    with user_store.transaction() as connection:
        settings, enabled_since = load_preferences(connection, user_id)
    since = enabled_since
    if not settings.enabled or not since or (settings.after_sync and not workspace.automatic_sync_scope()):
        return
    digest_key = None
    presumed = False
    if settings.mode == "digest":
        completions = read_sync_runtime(workspace.database)["scheduled_completions"] if settings.after_sync else []
        slot = digest_slot(settings, completions, local, now)
        if slot is None:
            return
        digest_day, digest_key, presumed = slot
        since = {channel: date for channel, date in since.items() if date <= digest_day}
        if not since:
            return
        with user_store.transaction() as connection:
            queued = {row["channel"] for row in connection.execute("""
                SELECT channel FROM notification_batches WHERE user_id = ? AND region = ? AND digest_day = ?
            """, (user_id, region, digest_key))}
        since = {channel: date for channel, date in since.items() if channel not in queued}
        if not since:
            return
    start = min(since.values())
    if not settings.after_sync and (settings.mode == "per_book" or settings.cadence == "daily"):
        start = max(start, (day - timedelta(days=CATCH_UP_DAYS)).isoformat())
    if start > day.isoformat():
        return
    with summaries():
        calendar_books = calendar(
            workspace.database, start, day.isoformat(), workspace.settings.english_only,
            include_past=True, consolidate=False,
        )
    books = [
        release_snapshot(book) for book in calendar_books
        if book["marketplace"] == region and book.get("release_date_precision") == "day" and book["asin"]
    ]
    if presumed:
        for book in books:
            book["presumed"] = True
    elif not settings.presume_released:
        books = [book for book in books if not book["presumed"]]
    if not books:
        return
    with user_store.transaction() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if load_preferences(connection, user_id) != (settings, enabled_since):
            return  # Settings changed during the catalogue read.
        for channel in CHANNELS:
            if channel in since:
                queue_releases(connection, (user_id, region, channel), books=books,
                               settings=settings, since=since[channel], day=day, now=now, digest_key=digest_key)


async def notify_workspace(manager, user, now):
    store = manager.user_store

    def preferences():
        with store.transaction() as connection:
            return load_preferences(connection, user.id)

    settings, since = await run_in_threadpool(preferences)
    if not settings.enabled or not since:
        return
    workspace = await run_in_threadpool(manager.get, user)
    region = workspace.settings.region

    def available():
        return (manager.current(user.id) is workspace and not manager.maintaining
                and not workspace.importing and not workspace.changing_settings)

    if not available() or (settings.after_sync and not workspace.automatic_sync_scope()):
        return
    await run_in_threadpool(prepare_deliveries, store, workspace, now)
    for _ in range(20):
        if not available():
            return
        batch = await run_in_threadpool(claim_due, store, user.id, region, now)
        if batch is None or not available():
            return
        settings, since = await run_in_threadpool(preferences)
        channel = batch["channel"]
        if not settings.enabled or channel not in since or not available() or (
            settings.after_sync and not workspace.automatic_sync_scope()
        ):
            continue
        error = None
        try:
            await deliver(channel, settings, admin=user.is_admin, books=json.loads(batch["payload"]),
                          digest_day=batch["digest_day"][:10] if batch["digest_day"] else None)
        except DeliveryError as exc:
            error = str(exc)
        await run_in_threadpool(finish_delivery, store, batch["id"], datetime.now(timezone.utc), error)


async def scheduled_notifications(manager):
    limit = asyncio.Semaphore(4)

    async def check(user, now):
        async with limit:
            try:
                await notify_workspace(manager, user, now)
            except Exception:
                logger.exception("release notification check failed for account %s", user.id)

    while True:
        try:
            users = await run_in_threadpool(manager.user_store.list, include_inactive=False)
            now = datetime.now(timezone.utc)
            await asyncio.gather(*(check(user, now) for user in users if not user.password_is_default))
        except Exception:
            logger.exception("release notification scheduler check failed")
        await asyncio.sleep(15)
