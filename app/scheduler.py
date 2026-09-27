"""Automatic catalogue checks with per-user failure isolation."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from .storage import maintain_storage
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger("audiblecalendar")


async def sync_due_workspaces(manager, *, now=None):
    if manager.maintaining:
        return None
    current = now or datetime.now(timezone.utc)
    next_due = None
    users = await run_in_threadpool(manager.user_store.list, include_inactive=False)
    for user in users:
        if manager.maintaining:
            return next_due
        if user.password_is_default:
            continue
        retry_at = manager.scheduler_retries.get(user.id)
        if retry_at and retry_at > current:
            next_due = min(next_due or retry_at, retry_at)
            continue
        try:
            workspace = await run_in_threadpool(manager.get, user)
            if manager.maintaining or workspace.importing or workspace.changing_settings or workspace.sync_service.sync_in_progress:
                continue
            due = await run_in_threadpool(workspace.automatic_sync_due_at, current)
            # Admission must be rechecked after the threadpool yield.
            if (manager.maintaining or manager.current(user.id) is not workspace or workspace.importing
                    or workspace.changing_settings or workspace.sync_service.sync_in_progress):
                continue
            if due is not None and due <= current:
                try:
                    result = await workspace.sync_service.sync_all(scope=workspace.automatic_sync_scope())
                    logger.info("scheduled sync finished for user %s: %s", user.id, result.get("status"))
                finally:
                    workspace.next_scheduled_sync_at = None
            elif due is not None:
                next_due = min(next_due or due, due)
            manager.scheduler_errors.pop(user.id, None)
            manager.scheduler_retries.pop(user.id, None)
        except Exception:
            logger.exception("scheduled workspace check failed for user %s", user.id)
            manager.scheduler_errors[user.id] = "Automatic checks failed for this library; retrying in five minutes."
            manager.scheduler_retries[user.id] = current + timedelta(minutes=5)
    active_ids = {user.id for user in users}
    manager.scheduler_errors = {key: value for key, value in manager.scheduler_errors.items() if key in active_ids}
    manager.scheduler_retries = {key: value for key, value in manager.scheduler_retries.items() if key in active_ids}
    return next_due


async def scheduled_sync(manager, changed):
    next_maintenance = monotonic() + 60
    while True:
        changed.clear()
        try:
            next_due = await sync_due_workspaces(manager)
            manager.scheduler_error = None
            workspaces = list(manager._workspaces.values())
            if manager.storage_policy.cleanup_enabled and monotonic() >= next_maintenance and not any(
                workspace.active_requests or workspace.importing or workspace.changing_settings
                or workspace.sync_service.sync_in_progress for workspace in workspaces
            ):
                manager.maintaining = True
                try:
                    manager.storage_status = await run_in_threadpool(maintain_storage, manager)
                    next_maintenance = monotonic() + 6 * 3600
                except Exception:
                    logger.exception("cache maintenance failed; retrying in one hour")
                    next_maintenance = monotonic() + 3600
                finally:
                    manager.maintaining = False
            timeout = 30 if next_due is None else max(0.1, min(30, (next_due - datetime.now(timezone.utc)).total_seconds()))
        except Exception:
            logger.exception("scheduler iteration failed; retrying in thirty seconds")
            manager.scheduler_error = "Automatic checks are unavailable; retrying shortly."
            timeout = 30
        try:
            await asyncio.wait_for(changed.wait(), timeout=timeout)
        except TimeoutError:
            pass
