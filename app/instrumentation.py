"""Lightweight in-process counters for upstream requests and cache outcomes."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from typing import Any


METRIC_KEYS = (
    "network_requests",
    "audible_requests",
    "audnexus_requests",
    "cache_hits",
    "cache_misses",
    "cache_stale",
    "cache_bypasses",
    "stale_fallbacks",
    "request_errors",
    "sync_deduplications",
    "lock_wait_reuses",
    "pagination_empty",
    "pagination_short",
    "pagination_past",
    "pagination_limit",
    "pagination_repeat",
)

OPERATIONS = frozenset({"search", "follow", "detail", "category", "sync_followed", "sync_favourites"})
FAMILIES = frozenset({"catalog_search", "category_page", "product", "similar", "audnexus_book"})


def source_operation(name: str):
    """Attribute adapter counters to one top-level user operation."""
    def decorate(function):
        @wraps(function)
        async def wrapped(self, *args, **kwargs):
            metrics = getattr(self, "metrics", None)
            operation = name(self, *args, **kwargs) if callable(name) else name
            with metrics.operation(operation) if metrics else nullcontext():
                return await function(self, *args, **kwargs)
        return wrapped
    return decorate


def record_pagination(target: Any, reason: str) -> None:
    metrics = getattr(target, "metrics", None) or getattr(getattr(target, "audible", None), "_metrics", None)
    if metrics is not None:
        metrics.increment("pagination_" + reason)


class SourceMetrics:
    """Thread-safe monotonic counters shared by the catalogue adapters."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._values = {key: 0 for key in METRIC_KEYS}
        self._operations = {name: {key: 0 for key in METRIC_KEYS} for name in OPERATIONS}
        self._families = {name: {key: 0 for key in METRIC_KEYS} for name in FAMILIES}
        self._operation_context = ContextVar("source_metric_operation", default=None)
        self._family_context = ContextVar("source_metric_family", default=None)

    @contextmanager
    def operation(self, name: str):
        if name not in OPERATIONS:
            raise ValueError("Unknown source operation")
        # A follow or sync may call another measured helper; attribute its
        # requests to the outer user action rather than counting it twice.
        if self._operation_context.get() is not None:
            yield
            return
        token = self._operation_context.set(name)
        try:
            yield
        finally:
            self._operation_context.reset(token)

    @contextmanager
    def family(self, name: str):
        if name not in FAMILIES:
            raise ValueError("Unknown source endpoint family")
        token = self._family_context.set(name)
        try:
            yield
        finally:
            self._family_context.reset(token)

    def increment(self, key: str, amount: int = 1) -> None:
        if key not in self._values:
            raise ValueError(f"Unknown source metric: {key}")
        self._record((key,), amount)

    def _record(self, keys: tuple[str, ...], amount: int = 1) -> None:
        with self._lock:
            operation = self._operation_context.get()
            family = self._family_context.get()
            for key in keys:
                self._values[key] += amount
                if operation:
                    self._operations[operation][key] += amount
                if family:
                    self._families[family][key] += amount

    def record_network_request(self, source: str) -> None:
        source_key = f"{source}_requests"
        self._record(("network_requests", source_key) if source_key in self._values else ("network_requests",))

    def snapshot(self, operation: str | None = None) -> dict[str, int]:
        with self._lock:
            return dict(self._operations[operation] if operation else self._values)

    def breakdown(self) -> dict[str, dict[str, dict[str, int]]]:
        with self._lock:
            return {
                "operations": {
                    name: {key: count for key, count in values.items() if count}
                    for name, values in self._operations.items() if any(values.values())
                },
                "families": {
                    name: {key: count for key, count in values.items() if count}
                    for name, values in self._families.items() if any(values.values())
                },
            }

    def since(self, baseline: dict[str, Any] | None, operation: str | None = None) -> dict[str, int]:
        previous = baseline or {}
        current = self.snapshot(operation)
        result: dict[str, int] = {}
        for key in METRIC_KEYS:
            try:
                earlier = int(previous.get(key, 0))
            except (TypeError, ValueError):
                earlier = 0
            result[key] = max(0, current[key] - earlier)
        return result


class SyncProgressTracker:
    """Publish a consistent snapshot while a catalogue sync is running."""

    def __init__(self, metrics: SourceMetrics) -> None:
        self.metrics = metrics
        self._lock = Lock()
        self._progress: dict[str, Any] | None = None
        self._metrics_baseline: dict[str, int] | None = None
        self._operation: str | None = None

    def start(self, force: bool, operation: str | None = None) -> None:
        with self._lock:
            self._operation = operation
            self._metrics_baseline = self.metrics.snapshot(operation)
            self._progress = {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "force": bool(force),
                "phase": "preparing",
                "total_targets": 0,
                "completed_targets": 0,
                "current_kind": None,
                "current_name": None,
                "current_step": None,
                "current_page": None,
            }

    def set_plan(self, total_targets: int) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress["phase"] = "catalogue"
                self._progress["total_targets"] = total_targets

    def start_target(self, kind: str, name: str) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress.update({
                    "current_kind": kind,
                    "current_name": name,
                    "current_step": None,
                    "current_page": None,
                })

    def set_step(self, step: str, page: int | None = None) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress["current_step"] = step
                self._progress["current_page"] = page

    def complete_target(self) -> None:
        with self._lock:
            if self._progress is not None:
                self._progress["completed_targets"] += 1

    def metrics_since_start(self) -> dict[str, int]:
        with self._lock:
            baseline = dict(self._metrics_baseline or {})
            operation = self._operation
        return self.metrics.since(baseline, operation)

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            if self._progress is None:
                return None
            progress = dict(self._progress)
            baseline = dict(self._metrics_baseline or {})
            operation = self._operation
        progress["metrics"] = self.metrics.since(baseline, operation)
        return progress

    def clear(self) -> None:
        with self._lock:
            self._progress = None
            self._metrics_baseline = None
            self._operation = None
