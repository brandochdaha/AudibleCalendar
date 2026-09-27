"""One active regional library per user, with shared upstream response caches."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from threading import RLock

from .config import Settings
from .cache import SourceCache
from .sources.http import HttpPool
from .db import Database
from .image_cache import ImageCache
from .regional_databases import prepare_user_regions, regional_path, validate_region
from .settings_store import load_user_settings, save_settings, save_user_settings, values_from_settings
from .sync import SyncService
from .sync_runtime import next_automatic_sync_at, read_sync_runtime
from .users import UserRecord, UserStore
from .storage_policy import load_storage_policy


@dataclass
class Workspace:
    user: UserRecord
    database: Database
    settings: Settings
    sync_service: SyncService
    image_cache: ImageCache
    next_scheduled_sync_at: datetime | None = None
    active_requests: int = 0
    importing: bool = False
    changing_settings: bool = False
    _schedule_key: tuple | None = field(default=None, repr=False)

    def automatic_sync_due_at(self, now: datetime | None = None) -> datetime | None:
        runtime = read_sync_runtime(self.database)
        scope = self.automatic_sync_scope()
        if scope is None:
            self.next_scheduled_sync_at = None
            return None
        interval_hours = (
            self.settings.sync_interval_hours
            if scope == "followed"
            else self.settings.favourites_sync_interval_hours
        )
        key = (runtime.get("last_completed_at"), interval_hours, scope, runtime.get("last_status"))
        if self.next_scheduled_sync_at is None or key != self._schedule_key:
            self.next_scheduled_sync_at = next_automatic_sync_at(key[0], interval_hours, now=now)
            if runtime.get("last_status") in {"partial", "error", "interrupted"}:
                from .sync_runtime import parse_runtime_datetime
                completed = parse_runtime_datetime(runtime.get("last_completed_at"))
                if completed:
                    self.next_scheduled_sync_at = max(
                        completed + timedelta(minutes=5),
                        (now or datetime.now(timezone.utc)) + timedelta(seconds=5),
                    )
            self._schedule_key = key
        return self.next_scheduled_sync_at

    def automatic_sync_scope(self) -> str | None:
        if self.settings.automatic_sync_enabled:
            return "followed"
        if self.settings.favourites_sync_enabled:
            return "favourites"
        return None


class WorkspaceManager:
    def __init__(self, user_store: UserStore, base_settings: Settings):
        self.user_store = user_store
        self.base_settings = base_settings
        self.defaults = values_from_settings(base_settings)
        self._workspaces: dict[int, Workspace] = {}
        self._lock = RLock()
        self.scheduler_errors = {}
        self.scheduler_retries = {}
        self.scheduler_error = None
        self.storage_policy = load_storage_policy(user_store)
        self.storage_status = {}
        self.maintaining = False
        cache_database = Database(base_settings.database_path.parent / "catalogue-cache.db")
        cache_database.initialize()
        self.source_cache = SourceCache(cache_database)
        self.http_pool = HttpPool()
        self.artwork_directory = base_settings.database_path.parent / "artwork"

    def get(self, user: UserRecord | int) -> Workspace:
        user_record = user if isinstance(user, UserRecord) else self.user_store.get(user)
        if user_record is None:
            raise ValueError("User not found")
        with self._lock:
            existing = self._workspaces.get(user_record.id)
            if existing is not None:
                existing.user = user_record
                return existing
            values = load_user_settings(self.user_store, user_record.id)
            if "region" not in values:
                values = prepare_user_regions(user_record, self.defaults)
                save_user_settings(self.user_store, user_record.id, values)
            workspace = self.open_region(user_record, {**self.defaults, **values})
            self._workspaces[user_record.id] = workspace
            return workspace

    def open_region(self, user: UserRecord, values: dict[str, Any]) -> Workspace:
        """Prepare a library without changing the user's active selection."""
        database = Database(regional_path(user, values["region"]))
        new_database = not database.path.exists()
        database.initialize(seed_demo=new_database and self.base_settings.demo_data and user.id == 1)
        if new_database:
            save_settings(database, {"region": values["region"]})
        validate_region(database, values["region"])
        settings = replace(self.base_settings, database_path=database.path, **values)
        return Workspace(
            user=user,
            database=database,
            settings=settings,
            sync_service=SyncService(database, settings, source_cache=self.source_cache, http_pool=self.http_pool),
            image_cache=ImageCache(database, shared_directory=self.artwork_directory, http_pool=self.http_pool),
        )

    def activate(self, workspace: Workspace, user: UserRecord, values: dict[str, Any]) -> None:
        """Publish only after account settings have committed successfully."""
        with self._lock:
            workspace.user = user
            for key, value in values.items():
                setattr(workspace.settings, key, value)
            workspace.next_scheduled_sync_at = None
            self._workspaces[user.id] = workspace

    def current(self, user_id: int) -> Workspace | None:
        # Publication happens on the event loop. Read without opening a DB or
        # taking the initialization lock while completing request admission.
        return self._workspaces.get(user_id)

    def primary(self) -> Workspace:
        users = self.user_store.list(include_inactive=False)
        if not users:
            raise RuntimeError("No active users are configured")
        return self.get(users[0])

    def all(self) -> list[Workspace]:
        return [self.get(user) for user in self.user_store.list(include_inactive=False)]
