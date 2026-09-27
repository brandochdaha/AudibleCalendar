"""Idle-time maintenance of disposable caches and opt-in backup retention."""

import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import Database
from .storage_policy import StoragePolicy


def _prune_responses(database: Database, policy: StoragePolicy) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=policy.cache_days)).isoformat()
    with database.transaction() as connection:
        rows = connection.execute("SELECT cache_key, fetched_at, length(CAST(payload AS BLOB)) + length(CAST(cache_key AS BLOB)) AS bytes FROM source_cache ORDER BY fetched_at DESC").fetchall()
        kept = 0
        obsolete = []
        for row in rows:
            if row["fetched_at"] < cutoff or (policy.response_bytes and kept + row["bytes"] > policy.response_bytes):
                obsolete.append((row["cache_key"],))
            else:
                kept += row["bytes"]
        connection.executemany("DELETE FROM source_cache WHERE cache_key = ?", obsolete)
    if obsolete:
        connection = database.connect()
        try:
            free = connection.execute("PRAGMA freelist_count").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            if free > 4096 and free > pages // 4:
                connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    return {"response_bytes": kept, "responses_removed": len(obsolete)}


def cache_databases(manager):
    """Locate live caches, excluding migration and import backups."""
    databases = {manager.source_cache.db.path}
    regional_paths = []
    for user in manager.user_store.list():
        regional_paths.extend(path for path in (user.data_path.parent / "regions").glob("*/audiblecalendar.db")
                              if not path.is_symlink() and not path.parent.is_symlink())
    databases.update(regional_paths)
    return databases, regional_paths


def measure_cache_usage(manager) -> dict:
    """Measure cached payloads and cover files without modifying them."""
    databases, regional_paths = cache_databases(manager)
    response_bytes = 0
    for path in databases:
        with Database(path).transaction() as connection:
            row = connection.execute(
                """SELECT COALESCE(SUM(
                    length(CAST(payload AS BLOB)) + length(CAST(cache_key AS BLOB))
                ), 0) FROM source_cache"""
            ).fetchone()
            response_bytes += int(row[0] or 0)

    artwork_bytes = 0
    directories = dict.fromkeys([manager.artwork_directory, *(path.parent / "images" for path in regional_paths)])
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir():
            continue
        for path in directory.iterdir():
            if (path.is_symlink() or not path.is_file()
                    or not re.fullmatch(r"[a-f0-9]{64}(?:\.(?:jpg|png|webp|gif|avif))?", path.name)):
                continue
            try:
                artwork_bytes += path.stat().st_size
            except OSError:
                continue

    return {
        "response_bytes": response_bytes,
        "artwork_bytes": artwork_bytes,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def maintain_storage(manager) -> dict:
    """Called only while API admissions are paused and all workspaces are idle."""
    policy = manager.storage_policy
    if not policy.cleanup_enabled:
        return {"cleanup_enabled": False}
    databases, regional_paths = cache_databases(manager)
    result = {"response_bytes": 0, "responses_removed": 0, "artwork_bytes": 0, "artwork_removed": 0, "backups_removed": 0}
    for path in databases:
        stats = _prune_responses(Database(path), policy)
        for key, value in stats.items():
            result[key] += value

    images = []
    cutoff = time.time() - policy.artwork_days * 86400
    directories = [manager.artwork_directory, *(path.parent / "images" for path in regional_paths)]
    for directory in directories:
        if directory.is_symlink() or not directory.exists():
            continue
        for path in directory.iterdir():
            if path.is_file() and not path.is_symlink() and re.fullmatch(r"[a-f0-9]{64}(?:\.(?:jpg|png|webp|gif|avif))?", path.name):
                info = path.stat()
                images.append((info.st_mtime, info.st_size, path))
    for stamp, size, path in sorted(images, reverse=True):
        if (policy.artwork_expiry_enabled and stamp < cutoff) or (policy.artwork_bytes and result["artwork_bytes"] + size > policy.artwork_bytes):
            path.unlink(missing_ok=True)
            result["artwork_removed"] += 1
        else:
            result["artwork_bytes"] += size
    for path in regional_paths:
        image_directory = path.parent / "images"
        with Database(path).transaction() as connection:
            missing = [(row[0],) for row in connection.execute("SELECT file_name FROM image_cache")
                       if not re.fullmatch(r"[a-f0-9]{64}", row[0]) or not (image_directory / row[0]).is_file()]
            connection.executemany("DELETE FROM image_cache WHERE file_name = ?", missing)
        if policy.backup_keep:
            backup_directory = path.parent / "backups"
            if backup_directory.is_symlink():
                continue
            backups = sorted((item for item in backup_directory.glob("audiblecalendar.pre-import.*.db")
                              if item.is_file() and not item.is_symlink()), key=lambda item: item.stat().st_mtime, reverse=True)
            for backup in backups[policy.backup_keep:]:
                backup.unlink()
                for suffix in ("-wal", "-shm"):
                    Path(str(backup) + suffix).unlink(missing_ok=True)
                image_backup = backup.with_suffix(".images")
                if image_backup.is_dir() and not image_backup.is_symlink():
                    shutil.rmtree(image_backup)
                result["backups_removed"] += 1
    return {**result, "checked_at": datetime.now(timezone.utc).isoformat()}
