"""Persist global sync timing and derive the next automatic check."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database


SYNC_RUNTIME_KEY = "catalogue_sync"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _empty_state() -> dict[str, Any]:
    return {
        "catalogue_revision": 0,
        "last_started_at": None,
        "last_completed_at": None,
        "last_success_at": None,
        "last_status": None,
        "last_error": None,
        "last_metrics": None,
    }


def _decode_state(value: str | None) -> dict[str, Any]:
    state = _empty_state()
    if value:
        try:
            saved = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            saved = None
        if isinstance(saved, dict):
            state.update({key: saved.get(key) for key in state if key in saved})
    try:
        state["catalogue_revision"] = max(0, int(state["catalogue_revision"] or 0))
    except (TypeError, ValueError):
        state["catalogue_revision"] = 0
    return state


def read_sync_runtime(db: Database) -> dict[str, Any]:
    """Return persisted global sync state without contacting a catalogue."""

    with db.transaction() as connection:
        return _read_sync_runtime(connection)


def _read_sync_runtime(connection: Any) -> dict[str, Any]:
    row = connection.execute(
        "SELECT state_value FROM app_runtime_state WHERE state_key = ?",
        (SYNC_RUNTIME_KEY,),
    ).fetchone()
    return _decode_state(row["state_value"] if row else None)


def _write_sync_runtime(connection: Any, state: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO app_runtime_state (state_key, state_value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value=excluded.state_value,
            updated_at=excluded.updated_at
        """,
        (SYNC_RUNTIME_KEY, json.dumps(state, ensure_ascii=False, separators=(",", ":"))),
    )


def mark_sync_started(db: Database, *, at: datetime | None = None) -> dict[str, Any]:
    with db.transaction() as connection:
        state = _read_sync_runtime(connection)
        state["last_started_at"] = (at or _now()).astimezone(timezone.utc).isoformat()
        state["last_status"] = "running"
        state["last_error"] = None
        _write_sync_runtime(connection, state)
    return state


def mark_sync_completed(
    db: Database,
    status: str,
    *,
    error: str | None = None,
    metrics: dict[str, int] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Record a completed attempt and advance the browser-visible revision."""

    with db.transaction() as connection:
        state = _read_sync_runtime(connection)
        completed_at = (at or _now()).astimezone(timezone.utc).isoformat()
        state["last_completed_at"] = completed_at
        state["last_status"] = status
        state["last_error"] = error
        state["last_metrics"] = dict(metrics) if metrics is not None else None
        state["catalogue_revision"] = int(state["catalogue_revision"]) + 1
        if status == "ok":
            state["last_success_at"] = completed_at
        _write_sync_runtime(connection, state)
    return state


def parse_runtime_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def next_automatic_sync_at(
    last_completed_at: Any,
    interval_hours: float,
    *,
    now: datetime | None = None,
    overdue_delay_seconds: int = 5,
) -> datetime:
    """Schedule from persisted completion time, with a brief startup grace."""

    current = (now or _now()).astimezone(timezone.utc)
    completed = parse_runtime_datetime(last_completed_at)
    if completed is not None:
        due = completed + timedelta(hours=max(1, interval_hours))
        if due > current:
            return due
    return current + timedelta(seconds=max(0, overdue_delay_seconds))
