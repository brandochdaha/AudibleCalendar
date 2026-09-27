"""Shared response caching and per-operation Audible freshness observations."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic

from ..cache import CacheEntry
from starlette.concurrency import run_in_threadpool


class SourceError(RuntimeError):
    """A recoverable upstream failure, with a bounded retry delay."""

    def __init__(self, message, *, retry_after=30):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class SourceReads:
    force: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    stale: bool = False


_reads = ContextVar("audible_source_reads", default=None)


@contextmanager
def capture_source_reads(*, force=False, started_at=None):
    """Keep concurrent detail requests and sync operations independent."""
    reads = SourceReads(force=force)
    if started_at is not None:
        reads.started_at = started_at
    token = _reads.set(reads)
    try:
        yield reads
    finally:
        _reads.reset(token)


def _observed(source, entry, max_age=0, *, stale=False):
    reads = _reads.get()
    if source == "audible" and reads is not None:
        reads.stale |= stale
        if entry is not None:
            reads.fetched_at = min(reads.fetched_at or entry.fetched_at, entry.fetched_at)
            if max_age:
                expires_at = entry.fetched_at + timedelta(seconds=max_age)
                reads.expires_at = min(reads.expires_at or expires_at, expires_at)


async def cached_response(cache, local_cache, source, parameters, max_age, request, *, force=False, metrics=None):
    """Cache successful reads and briefly suppress repeated failed requests."""
    reads = _reads.get()
    forced_details = source == "audible" and reads is not None and reads.force
    force = force or forced_details
    requested_at = reads.started_at if forced_details else datetime.now(timezone.utc)
    if cache is None or not max_age:
        payload = await request()
        _observed(source, CacheEntry(payload, datetime.now(timezone.utc)))
        return payload
    key = cache.make_key(source, "get", parameters)
    lock = cache.lock_for(key)
    waited = lock.locked()
    async with lock:
        cached = await run_in_threadpool(cache.read_with_fallback, key, local_cache)
        if cache.is_fresh(cached, max_age) and (not force or cached.fetched_at >= requested_at):
            if metrics:
                metrics.increment("cache_hits")
                if waited:
                    metrics.increment("lock_wait_reuses")
            _observed(source, cached, max_age)
            return cached.payload
        if metrics:
            metrics.increment("cache_bypasses" if force and cached else "cache_stale" if cached else "cache_misses")
        try:
            failure = cache.failures.get(key)
            if failure and failure[0] > monotonic():
                raise SourceError(failure[1], retry_after=failure[0] - monotonic())
            cache.failures.pop(key, None)
            try:
                payload = await request()
            except SourceError as exc:
                if len(cache.failures) >= 256:
                    cache.failures.pop(next(iter(cache.failures)))
                cache.failures[key] = (monotonic() + exc.retry_after, str(exc))
                raise
        except SourceError:
            _observed(source, cached, max_age, stale=True)
            if cached:
                if metrics:
                    metrics.increment("stale_fallbacks")
                return cached.payload
            raise
        fetched_at = datetime.now(timezone.utc)
        await run_in_threadpool(cache.write, key, source, "get", payload, fetched_at=fetched_at)
        _observed(source, CacheEntry(payload, fetched_at), max_age)
        return payload
