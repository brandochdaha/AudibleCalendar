"""Persisted, user-editable application settings."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from .db import Database
from .api_models import SettingsUpdate


EDITABLE_SETTINGS = (
    "region",
    "english_only",
    "audnexus_enabled",
    "watchlist_chips_always_visible",
    "header_sync_summary_visible",
    "header_sync_dates_visible",
    "header_sync_date_style",
    "close_settings_on_save",
    "automatic_sync_enabled",
    "sync_interval_hours",
    "favourites_sync_enabled",
    "favourites_sync_interval_hours",
    "date_display",
    "time_zone",
    "default_release_scope",
    "default_category_id",
    "default_category_window",
    "default_category_days",
    "cache_detail_minutes",
    "cache_search_minutes",
    "cache_author_hours",
    "cache_series_hours",
    "cache_upcoming_hours",
    "cache_far_future_hours",
    "cache_released_days",
    "author_merge_min_shared_series",
)


def load_settings(db: Database) -> dict[str, Any]:
    with db.transaction() as connection:
        rows = connection.execute(
            "SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN ({})".format(
                ", ".join("?" for _ in EDITABLE_SETTINGS)
            ),
            EDITABLE_SETTINGS,
        ).fetchall()
    return _decode_settings(rows)


def _decode_settings(rows) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in rows:
        try:
            values.update(SettingsUpdate.model_validate({row["setting_key"]: json.loads(row["setting_value"])}).model_dump(exclude_none=True))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return values


def load_user_settings(user_store, user_id: int) -> dict[str, Any]:
    with user_store.transaction() as connection:
        rows = connection.execute(
            "SELECT setting_key, setting_value FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchall()
    return _decode_settings(rows)


def save_user_settings(user_store, user_id: int, values: dict[str, Any], *, connection=None) -> None:
    unknown = set(values) - set(EDITABLE_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    with (nullcontext(connection) if connection is not None else user_store.transaction()) as connection:
        connection.executemany(
            """INSERT INTO user_settings (user_id, setting_key, setting_value)
            VALUES (?, ?, ?) ON CONFLICT(user_id, setting_key) DO UPDATE SET
                setting_value=excluded.setting_value, updated_at=CURRENT_TIMESTAMP""",
            ((user_id, key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()),
        )


def save_settings(db: Database, values: dict[str, Any], *, connection=None) -> None:
    unknown = set(values) - set(EDITABLE_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    with (nullcontext(connection) if connection is not None else db.transaction()) as connection:
        for key, value in values.items():
            connection.execute(
                """
                INSERT INTO app_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value=excluded.setting_value,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )


def values_from_settings(settings: Any) -> dict[str, Any]:
    return {key: getattr(settings, key) for key in EDITABLE_SETTINGS}
