"""Small persistent cache for upstream Audible/Audnexus responses.

The catalogue is local-first, so cached responses are an optimisation rather
than the source of truth.  Entries are kept in SQLite with their fetch time;
callers choose a freshness window appropriate to the data being requested.
"""

from __future__ import annotations

import json
import asyncio
import weakref
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .db import Database


@dataclass(frozen=True)
class CacheEntry:
    payload: Any
    fetched_at: datetime


class SourceCache:
    """Persistent response cache shared by the source adapters."""

    def __init__(self, db: Database):
        self.db = db
        self.locks = weakref.WeakValueDictionary()
        self.failures = {}

    def lock_for(self, key: str) -> asyncio.Lock:
        return self.locks.setdefault(key, asyncio.Lock())

    def read_with_fallback(self, cache_key: str, local: SourceCache | None = None) -> CacheEntry | None:
        """Reuse existing/imported user responses without publishing them globally."""
        shared = self.read(cache_key)
        if local is None or local.db.path == self.db.path:
            return shared
        previous = local.read(cache_key)
        if previous and (shared is None or previous.fetched_at > shared.fetched_at):
            return previous
        return shared

    @staticmethod
    def make_key(source: str, operation: str, parameters: dict[str, Any]) -> str:
        return json.dumps(
            {"source": source, "operation": operation, "parameters": parameters},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def read(self, cache_key: str) -> CacheEntry | None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT payload, fetched_at FROM source_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
            fetched_at = datetime.fromisoformat(row["fetched_at"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return CacheEntry(payload=payload, fetched_at=fetched_at)

    def write(
        self,
        cache_key: str,
        source: str,
        operation: str,
        payload: Any,
        *,
        fetched_at: datetime | None = None,
    ) -> None:
        try:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            # A malformed upstream response should never make a catalogue
            # request fail merely because it cannot be cached.
            return
        now = (fetched_at or datetime.now(timezone.utc)).isoformat()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO source_cache (cache_key, source, operation, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    source=excluded.source,
                    operation=excluded.operation,
                    payload=excluded.payload,
                    fetched_at=excluded.fetched_at
                """,
                (cache_key, source, operation, encoded, now),
            )

    def delete(self, cache_key: str) -> None:
        """Remove one persisted response so the next read can refresh it."""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM source_cache WHERE cache_key = ?", (cache_key,))

    @staticmethod
    def is_fresh(entry: CacheEntry | None, max_age_seconds: float, now: datetime | None = None) -> bool:
        if not entry:
            return False
        current = now or datetime.now(timezone.utc)
        return (current - entry.fetched_at).total_seconds() <= max_age_seconds

    def product_max_age_seconds(
        self,
        asin: str,
        marketplace: str,
        *,
        upcoming_hours: float = 6.0,
        far_future_hours: float = 24.0,
        released_days: float = 30.0,
        today: date | None = None,
    ) -> float:
        """Choose product freshness from the locally known release date.

        Upcoming books are checked frequently, while released books are kept
        for much longer. Unknown products use the far-future window until a
        release date is learned.
        """

        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT release_date FROM books WHERE marketplace = ? AND asin = ?",
                (marketplace, asin),
            ).fetchone()
        release_value = row["release_date"] if row else None
        if not release_value:
            return far_future_hours * 60 * 60
        try:
            release = date.fromisoformat(str(release_value)[:10])
        except ValueError:
            return far_future_hours * 60 * 60
        days_until_release = (release - (today or date.today())).days
        if days_until_release >= 0:
            return (upcoming_hours if days_until_release <= 30 else far_future_hours) * 60 * 60
        return released_days * 24 * 60 * 60
