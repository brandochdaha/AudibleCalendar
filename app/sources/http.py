"""A reusable HTTP connection pool, closed with the application lifespan."""

import httpx
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from time import monotonic

from .request_cache import SourceError


class HttpPool:
    def __init__(self):
        self._client = None
        self.audible_slots = asyncio.Semaphore(2)
        self._audible_start_lock = asyncio.Lock()
        self.audible_interval = 0.5
        self._next_audible_start = 0.0
        self._audible_cooldown_until = 0.0

    def defer_audible(self, retry_after=None, *, default=30):
        delay = float(default)
        if retry_after:
            try:
                seconds = float(retry_after)
                if isfinite(seconds):
                    delay = max(delay, seconds)
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(delay, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        self._audible_cooldown_until = max(self._audible_cooldown_until, monotonic() + delay)
        return delay

    def _check_audible_cooldown(self):
        remaining = self._audible_cooldown_until - monotonic()
        if remaining > 0:
            raise SourceError("Audible requests are temporarily paused after an upstream failure. Try again shortly.",
                              retry_after=remaining)

    @asynccontextmanager
    async def audible_request(self):
        """Limit starts and in-flight requests across every user and region."""
        self._check_audible_cooldown()
        async with self.audible_slots:
            async with self._audible_start_lock:
                self._check_audible_cooldown()
                delay = self._next_audible_start - monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self._check_audible_cooldown()
                self._next_audible_start = monotonic() + self.audible_interval
            yield

    def client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(follow_redirects=False)
        return self._client

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
