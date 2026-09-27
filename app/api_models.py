"""Validated request contracts shared by routes and persisted settings."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from .input_validation import validate_catalogue_dates
from .storage_policy import StoragePolicy
from .time_zone import validate_time_zone


class CatalogueRequest(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def valid_dates(cls, value):
        validate_catalogue_dates(value)
        return value


class WatchRequest(CatalogueRequest):
    kind: Literal["author", "series", "book"]
    asin: str = Field(min_length=1, max_length=80)
    seed_asin: str | None = Field(default=None, max_length=80)
    name: str | None = None
    title: str | None = None
    marketplace: str | None = None
    image_url: str | None = None
    description: str | None = None
    release_date: str | None = None
    authors: list[dict[str, Any]] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)
    books: list[dict[str, Any]] = Field(default_factory=list)
    # Book follows may explicitly opt selected credited authors into author
    # follows. An empty list means follow the book only.
    follow_author_asins: list[str] = Field(default_factory=list)
    # When a book is selected from an author's detail view, keep that
    # relationship scoped to the author instead of making the book global.
    author_scope_asins: list[str] = Field(default_factory=list)
    payload: dict[str, Any] | None = None


class AuthorGroupWatchRequest(BaseModel):
    members: list[WatchRequest] = Field(min_length=1, max_length=50)


class AuthorSearchMember(BaseModel):
    asin: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    source_asins: list[str] | None = Field(default=None, max_length=1000)
    source_groups: list[list[str]] | None = Field(default=None, max_length=1000)


class AuthorSearchDecisionRequest(BaseModel):
    members: list[AuthorSearchMember] = Field(min_length=2, max_length=2)
    primary_asin: str = Field(min_length=1, max_length=80)
    decision: Literal["merge", "separate"]
    manual: bool = False
    override_separation: bool = False


class AuthorSplitRequest(BaseModel):
    primary_asin: str = Field(min_length=1, max_length=80)
    split_asins: list[str] = Field(min_length=1, max_length=50)


class AuthorSeriesRequest(CatalogueRequest):
    action: Literal["select", "deselect", "all", "none"] = "select"
    # The author-detail endpoint uses the same request for series selections
    # and standalone-release selections.  Keep the public route stable while
    # making the release scope explicit for the latter.
    release_kind: Literal["series", "book"] = "series"
    author_asin: str = Field(min_length=1, max_length=80)
    author_name: str = Field(min_length=1, max_length=160)
    series_asin: str | None = Field(default=None, max_length=80)
    series_name: str | None = Field(default=None, max_length=240)
    book_asin: str | None = Field(default=None, max_length=80)
    marketplace: str | None = None
    books: list[dict[str, Any]] = Field(default_factory=list)
    book: dict[str, Any] | None = None
    author: dict[str, Any] | None = None
    series: dict[str, Any] | None = None


class SettingsUpdate(BaseModel):
    storage_policy: StoragePolicy | None = None
    region: Literal["us", "uk", "ca", "au", "de", "fr", "es", "it", "jp", "in", "br"] | None = None
    english_only: bool | None = None
    audnexus_enabled: bool | None = None
    watchlist_chips_always_visible: bool | None = None
    header_sync_summary_visible: bool | None = None
    header_sync_dates_visible: bool | None = None
    header_sync_date_style: Literal["short", "relative"] | None = None
    close_settings_on_save: bool | None = None
    automatic_sync_enabled: bool | None = None
    sync_interval_hours: float | None = Field(default=None, ge=1, le=720)
    favourites_sync_enabled: bool | None = None
    favourites_sync_interval_hours: float | None = Field(default=None, ge=1, le=720)
    date_display: Literal["uk", "us", "iso"] | None = None
    time_zone: str | None = Field(default=None, min_length=1, max_length=255)
    default_release_scope: Literal["followed", "favourites"] | None = None
    default_category_id: str | None = Field(default=None, min_length=1, max_length=32)
    default_category_window: Literal["month_remaining", "fixed"] | None = None
    default_category_days: int | None = Field(default=None, ge=1, le=3650)
    cache_detail_minutes: float | None = Field(default=None, ge=1, le=1440)
    cache_search_minutes: float | None = Field(default=None, ge=1, le=1440)
    cache_author_hours: float | None = Field(default=None, ge=1, le=720)
    cache_series_hours: float | None = Field(default=None, ge=1, le=720)
    cache_upcoming_hours: float | None = Field(default=None, ge=1, le=720)
    cache_far_future_hours: float | None = Field(default=None, ge=1, le=720)
    cache_released_days: float | None = Field(default=None, ge=1, le=99999)
    author_merge_min_shared_series: int | None = Field(default=None, ge=1, le=100)
    auth_username: str | None = Field(default=None, min_length=1, max_length=160)
    auth_password: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str | None) -> str | None:
        return validate_time_zone(value) if value is not None else None


class ClearCacheRequest(BaseModel):
    cleanup_enabled: bool
    artwork_expiry_enabled: bool

    @model_validator(mode="after")
    def requires_cache_selection(self):
        if not self.cleanup_enabled and not self.artwork_expiry_enabled:
            raise ValueError("Select Automatic cleanup or Image expiry by age to clear cache")
        return self


class CategoryUpcomingRequest(BaseModel):
    category_id: str = Field(min_length=1, max_length=32)
    days: int = Field(default=365, ge=1, le=3650)
    force: bool = False


class UnfollowRequest(BaseModel):
    kind: Literal["series", "book"]
    entity_id: int = Field(ge=1)
    author_ids: list[int] = Field(default_factory=list)
    remove_direct: bool | None = None


class FavouriteRequest(BaseModel):
    kind: Literal["author", "series", "book"]
    entity_id: int = Field(ge=1)
    is_favourite: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=256)
    role: Literal["user"] = "user"


class UserUpdateRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=160)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    active: bool | None = None
    role: Literal["user"] | None = None
