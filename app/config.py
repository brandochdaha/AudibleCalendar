"""Environment-backed configuration for the local AudibleCalendar server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .category_catalog import DEFAULT_CATEGORY_ID
from .time_zone import validate_time_zone


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _choice(value: str | None, default: str, choices: set[str]) -> str:
    selected = (value or default).strip().lower()
    return selected if selected in choices else default


def _time_zone_choice(value: str | None, default: str, choices: set[str]) -> str:
    selected = (value or default).strip()
    selected = next((choice for choice in choices if choice.casefold() == selected.casefold()), selected)
    try:
        return validate_time_zone(selected)
    except ValueError:
        return default


def _database_path() -> Path:
    return Path(os.getenv("AUDIBLECALENDAR_DB") or ".data/audiblecalendar.db")


@dataclass
class Settings:
    database_path: Path
    region: str
    demo_data: bool
    english_only: bool
    audible_timeout: float
    audnexus_enabled: bool
    watchlist_chips_always_visible: bool
    header_sync_summary_visible: bool
    header_sync_dates_visible: bool
    header_sync_date_style: str
    close_settings_on_save: bool
    audnexus_base_url: str
    sync_page_size: int
    sync_max_pages: int
    automatic_sync_enabled: bool
    sync_interval_hours: float
    favourites_sync_enabled: bool
    favourites_sync_interval_hours: float
    date_display: str
    time_zone: str
    default_release_scope: str
    default_category_id: str
    default_category_window: str
    default_category_days: int
    cache_detail_minutes: float
    cache_search_minutes: float
    cache_author_hours: float
    cache_series_hours: float
    cache_upcoming_hours: float
    cache_far_future_hours: float
    cache_released_days: float
    author_merge_min_shared_series: int
    detect_browser_time_zone: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=_database_path(),
            region=os.getenv("AUDIBLE_REGION", "us").lower(),
            demo_data=_bool(
                os.getenv("AUDIBLECALENDAR_DEMO_DATA"),
                False,
            ),
            english_only=_bool(os.getenv("AUDIBLE_ENGLISH_ONLY"), True),
            audible_timeout=float(os.getenv("AUDIBLE_TIMEOUT", "20")),
            audnexus_enabled=_bool(os.getenv("AUDNEXUS_ENABLED"), True),
            watchlist_chips_always_visible=_bool(os.getenv("AUDIBLE_WATCHLIST_CHIPS_ALWAYS_VISIBLE"), False),
            header_sync_summary_visible=_bool(os.getenv("AUDIBLE_HEADER_SYNC_SUMMARY_VISIBLE"), False),
            header_sync_dates_visible=True,
            header_sync_date_style="relative",
            close_settings_on_save=_bool(os.getenv("AUDIBLE_CLOSE_SETTINGS_ON_SAVE"), True),
            audnexus_base_url=os.getenv("AUDNEXUS_BASE_URL", "https://api.audnex.us").rstrip("/"),
            sync_page_size=max(1, min(int(os.getenv("AUDIBLE_SYNC_PAGE_SIZE", "50")), 50)),
            sync_max_pages=max(1, int(os.getenv("AUDIBLE_SYNC_MAX_PAGES", "8"))),
            automatic_sync_enabled=_bool(os.getenv("AUDIBLE_AUTOMATIC_SYNC_ENABLED"), True),
            sync_interval_hours=max(1.0, float(os.getenv("AUDIBLE_SYNC_INTERVAL_HOURS", "24"))),
            favourites_sync_enabled=_bool(os.getenv("AUDIBLE_FAVOURITES_SYNC_ENABLED"), False),
            favourites_sync_interval_hours=max(1.0, float(os.getenv("AUDIBLE_FAVOURITES_SYNC_INTERVAL_HOURS", "24"))),
            date_display=_choice(os.getenv("AUDIBLE_DATE_DISPLAY"), "us", {"uk", "us", "iso"}),
            time_zone=_time_zone_choice(os.getenv("AUDIBLE_TIME_ZONE"), "America/Los_Angeles", {
                "America/Los_Angeles", "America/Denver", "America/Chicago", "America/New_York",
                "America/Toronto", "Europe/London", "Europe/Berlin", "Europe/Paris",
                "Europe/Madrid", "Europe/Rome", "Asia/Tokyo", "Asia/Kolkata",
                "Australia/Sydney", "America/Sao_Paulo", "UTC",
            }),
            default_release_scope=_choice(os.getenv("AUDIBLE_DEFAULT_RELEASE_SCOPE"), "favourites", {"followed", "favourites"}),
            default_category_id=os.getenv("AUDIBLE_DEFAULT_CATEGORY_ID", DEFAULT_CATEGORY_ID).strip() or DEFAULT_CATEGORY_ID,
            default_category_window=_choice(
                os.getenv("AUDIBLE_DEFAULT_CATEGORY_WINDOW"),
                "month_remaining",
                {"month_remaining", "fixed"},
            ),
            default_category_days=max(1, min(int(os.getenv("AUDIBLE_DEFAULT_CATEGORY_DAYS", "30")), 3650)),
            cache_detail_minutes=max(1.0, float(os.getenv("AUDIBLE_CACHE_DETAIL_MINUTES", "120"))),
            cache_search_minutes=max(1.0, float(os.getenv("AUDIBLE_CACHE_SEARCH_MINUTES", "15"))),
            cache_author_hours=max(1.0, float(os.getenv("AUDIBLE_CACHE_AUTHOR_HOURS", "24"))),
            cache_series_hours=max(1.0, float(os.getenv("AUDIBLE_CACHE_SERIES_HOURS", "24"))),
            cache_upcoming_hours=max(1.0, float(os.getenv("AUDIBLE_CACHE_UPCOMING_HOURS", "6"))),
            cache_far_future_hours=max(1.0, float(os.getenv("AUDIBLE_CACHE_FAR_FUTURE_HOURS", "168"))),
            cache_released_days=max(1.0, float(os.getenv("AUDIBLE_CACHE_RELEASED_DAYS", "3650"))),
            author_merge_min_shared_series=max(1, min(int(os.getenv("AUDIBLE_AUTHOR_MERGE_MIN_SHARED_SERIES", "1")), 100)),
            detect_browser_time_zone=not bool(os.getenv("AUDIBLE_TIME_ZONE", "").strip()),
        )
