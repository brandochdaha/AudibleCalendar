"""Check published AudibleCalendar releases without installing updates."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from starlette.concurrency import run_in_threadpool

from . import __version__


REPOSITORY = "brandochdaha/AudibleCalendar"
LATEST_RELEASE = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL = timedelta(days=1)
RELEASE_VERSION = re.compile(r"v?(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")
logger = logging.getLogger("audiblecalendar")


def version_parts(tag):
    match = RELEASE_VERSION.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(map(int, match.groups())) if match else None


class ReleaseChecker:
    def __init__(self, user_store):
        self.user_store = user_store
        self.checked_at = None
        self.latest_tag = None

    def available_update(self):
        installed = version_parts(__version__)
        latest = version_parts(self.latest_tag)
        if installed is None or latest is None or latest <= installed:
            return None
        return {
            "version": self.latest_tag.removeprefix("v"),
            "url": f"https://github.com/{REPOSITORY}/releases/tag/{self.latest_tag}",
        }

    def restore(self):
        with self.user_store.transaction() as connection:
            row = connection.execute(
                "SELECT setting_value FROM instance_settings WHERE setting_key = 'github_release_check'"
            ).fetchone()
        if row:
            try:
                cached = json.loads(row[0])
                checked_at = datetime.fromisoformat(cached["checked_at"])
                if checked_at.tzinfo is not None:
                    self.checked_at = checked_at
                    self.latest_tag = cached.get("latest_tag")
            except (AttributeError, KeyError, TypeError, ValueError):
                pass

    def persist(self):
        with self.user_store.transaction() as connection:
            connection.execute("""
                INSERT INTO instance_settings (setting_key, setting_value)
                VALUES ('github_release_check', ?) ON CONFLICT(setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
            """, (json.dumps({"checked_at": self.checked_at.isoformat(), "latest_tag": self.latest_tag}),))

    async def check_if_due(self, client, now):
        if self.checked_at is not None and now - self.checked_at < CHECK_INTERVAL:
            return
        self.checked_at = now
        try:
            response = await client.get(LATEST_RELEASE)
            if response.status_code == 404:
                self.latest_tag = None
            else:
                response.raise_for_status()
                release = response.json()
                tag = release.get("tag_name") if isinstance(release, dict) else None
                self.latest_tag = tag if version_parts(tag) else None
        except (httpx.HTTPError, ValueError) as exc:
            # Keep the last known result when GitHub is temporarily unreachable.
            logger.debug("GitHub release check failed: %s", exc)
        try:
            await run_in_threadpool(self.persist)
        except Exception:
            logger.warning("Could not save the GitHub release check result", exc_info=True)

    async def run(self):
        try:
            await run_in_threadpool(self.restore)
        except Exception:
            logger.warning("Could not load the previous GitHub release check", exc_info=True)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"AudibleCalendar/{__version__}"}
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            while True:
                await self.check_if_due(client, datetime.now(timezone.utc))
                due_at = self.checked_at + CHECK_INTERVAL
                delay = (due_at - datetime.now(timezone.utc)).total_seconds()
                await asyncio.sleep(max(1, delay))
