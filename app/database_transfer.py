"""Consistent archive export/import helpers for moving an installation."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .db import Database
from .input_validation import release_date
from .migrations import LATEST_SCHEMA_VERSION, SCHEMA
from .regional_databases import validate_region
from .settings_store import load_settings, load_user_settings, save_settings, save_user_settings


REQUIRED_TABLES = {"authors", "series", "books", "app_settings"}
ARCHIVE_FORMAT_VERSION = 2
ARCHIVE_APP_NAME = "AudibleCalendar"
DATABASE_MEMBER = "database/audiblecalendar.db"
IMAGES_MEMBER = "images/"
MAX_ARCHIVE_MEMBERS = 200_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024


class DatabaseTransferError(ValueError):
    """Raised when a database or archive cannot be safely transferred."""


def _backup(source_path: Path, destination_path: Path) -> None:
    Database(source_path).backup_to(destination_path)


def _create_database_copy(database: Database) -> Path:
    if str(database.path) == ":memory:":
        raise DatabaseTransferError("An in-memory database cannot be exported")
    database.path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".audiblecalendar-export-",
        suffix=".db",
        dir=database.path.parent,
    )
    os.close(descriptor)
    export_path = Path(name)
    try:
        _backup(database.path, export_path)
    except Exception:
        export_path.unlink(missing_ok=True)
        raise
    return export_path


def create_export(database: Database, *, settings: dict | None = None) -> Path:
    """Export the selected regional library and account preferences."""

    database_copy = _create_database_copy(database)
    descriptor, name = tempfile.mkstemp(
        prefix=".audiblecalendar-export-",
        suffix=".zip",
        dir=database.path.parent,
    )
    os.close(descriptor)
    export_path = Path(name)
    data_directory = database.path.parent
    image_directory = data_directory / "images"
    try:
        snapshot = Database(database_copy)
        if settings is not None:
            save_settings(snapshot, settings)
        region = load_settings(snapshot).get("region")
        if not region:
            raise DatabaseTransferError("Only regional databases can be exported")
        try:
            validate_region(snapshot, region)
        except ValueError as exc:
            raise DatabaseTransferError(str(exc)) from exc
        with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            manifest: dict[str, Any] = {
                "app": ARCHIVE_APP_NAME,
                "format_version": ARCHIVE_FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "region": region,
                "database": DATABASE_MEMBER,
                "images": IMAGES_MEMBER,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
            archive.write(database_copy, DATABASE_MEMBER)

            # Always include the directory marker. An empty cache in the source
            # should clear a stale cache at the destination during import.
            archive.writestr(IMAGES_MEMBER, "")
            if image_directory.is_dir():
                for image_path in sorted(image_directory.rglob("*")):
                    if not image_path.is_file() or image_path.is_symlink():
                        continue
                    archive.write(image_path, (Path(IMAGES_MEMBER) / image_path.relative_to(image_directory)).as_posix())

    except Exception:
        export_path.unlink(missing_ok=True)
        raise
    finally:
        database_copy.unlink(missing_ok=True)
    return export_path


def validate_database(path: Path) -> None:
    """Reject corrupt, unrelated, or too-new database files."""

    try:
        connection = sqlite3.connect(path)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick_check != "ok":
                raise DatabaseTransferError("The database failed SQLite integrity checks")
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if user_version not in (8, LATEST_SCHEMA_VERSION):
                raise DatabaseTransferError(
                    f"Unsupported database schema {user_version}; expected {LATEST_SCHEMA_VERSION}"
                )
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchone():
                raise DatabaseTransferError("Database triggers and views are not supported in archives")
            expected = sqlite3.connect(":memory:")
            try:
                expected.executescript(SCHEMA)
                for (table,) in expected.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                    if user_version == 8 and table == "author_search_evidence":
                        continue
                    required = {row[1] for row in expected.execute(f'PRAGMA table_info("{table}")')}
                    actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                    if not required.issubset(actual):
                        raise DatabaseTransferError(f"Missing or invalid {table} table")
                for (value,) in connection.execute("SELECT DISTINCT release_date FROM books WHERE release_date IS NOT NULL"):
                    try:
                        release_date(value)
                    except (TypeError, ValueError) as exc:
                        raise DatabaseTransferError("The database contains an invalid release date") from exc
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise DatabaseTransferError("The database contains broken relationships")
            finally:
                expected.close()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise DatabaseTransferError("The selected file is not a valid SQLite database") from exc
    if not REQUIRED_TABLES.issubset(tables):
        raise DatabaseTransferError("The selected database is not an AudibleCalendar database")


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
        raise DatabaseTransferError("The archive contains an unsafe file path")
    return path


def _archive_manifest(archive_path: Path) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DatabaseTransferError("The selected file is not a valid AudibleCalendar archive") from exc
    try:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise DatabaseTransferError("The archive contains too many files")
        seen: set[str] = set()
        total_size = 0
        for member in members:
            normalized = str(_safe_archive_name(member.filename))
            if normalized in seen:
                raise DatabaseTransferError("The archive contains duplicate file names")
            seen.add(normalized)
            if member.flag_bits & 1:
                raise DatabaseTransferError("Encrypted archives are not supported")
            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise DatabaseTransferError("The archive contains a symbolic link")
            total_size += member.file_size
            if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise DatabaseTransferError("The archive expands beyond the supported limit")
        try:
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise DatabaseTransferError("Archive manifest is too large")
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise DatabaseTransferError("The archive is missing a valid manifest") from exc
    finally:
        archive.close()

    if not isinstance(manifest, dict) or manifest.get("app") != ARCHIVE_APP_NAME:
        raise DatabaseTransferError("The selected archive is not an AudibleCalendar export")
    if manifest.get("format_version") != ARCHIVE_FORMAT_VERSION:
        raise DatabaseTransferError("The archive format is not supported by this app version")
    database_member = manifest.get("database")
    if database_member != DATABASE_MEMBER:
        raise DatabaseTransferError("The archive has an invalid database entry")
    names = {member.filename for member in members}
    if database_member not in names:
        raise DatabaseTransferError("The archive does not contain a database")
    if manifest.get("images") != IMAGES_MEMBER:
        raise DatabaseTransferError("The archive has an invalid image-cache entry")
    return manifest, members


def _extract_archive(archive_path: Path, destination: Path) -> dict[str, Any]:
    manifest, _ = _archive_manifest(archive_path)
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DatabaseTransferError("The selected file is not a valid AudibleCalendar archive") from exc
    try:
        for member in archive.infolist():
            relative = _safe_archive_name(member.filename)
            target = destination.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DatabaseTransferError("The archive could not be extracted") from exc
    finally:
        archive.close()
    return manifest


def backup_current(database: Database) -> Path | None:
    """Create a recoverable backup immediately before an import replaces data."""

    if str(database.path) == ":memory:" or not database.path.exists():
        return None
    backup_directory = database.path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_directory / f"{database.path.stem}.pre-import.{timestamp}.db"
    _backup(database.path, backup_path)
    return backup_path


def _image_backup_path(database: Database, backup_path: Path | None) -> Path:
    backup_directory = database.path.parent / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    if backup_path is not None:
        return backup_directory / f"{backup_path.stem}.images"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return backup_directory / f"{database.path.stem}.pre-import.{timestamp}.images"


def import_archive(database: Database, archive_path: Path, *, user_store=None, user_id=None) -> Path | None:
    """Replace only the selected region; preserve other libraries and logins."""

    if str(database.path) == ":memory:":
        raise DatabaseTransferError("An in-memory database cannot be replaced")
    data_directory = database.path.parent
    staging = Path(tempfile.mkdtemp(prefix=".audiblecalendar-import-", dir=data_directory))
    current_images = data_directory / "images"
    staged_images = staging / "images"
    image_backup: Path | None = None
    database_replaced = False
    images_replaced = False
    backup_path: Path | None = None
    try:
        manifest = _extract_archive(archive_path, staging)
        staged_database = staging / str(manifest["database"])
        validate_database(staged_database)
        region = load_settings(database).get("region")
        if not region or manifest.get("region") != region:
            raise DatabaseTransferError("Select the archive's region in Settings before importing it")
        staged = Database(staged_database)
        staged.initialize(seed_demo=False)
        try:
            validate_region(staged, region)
        except ValueError as exc:
            raise DatabaseTransferError(str(exc)) from exc
        imported_settings = load_settings(staged)
        with staged.transaction() as connection:
            connection.execute("DELETE FROM app_settings WHERE setting_key != 'region'")
        staged_images.mkdir(parents=True, exist_ok=True)

        backup_path = backup_current(database)
        if backup_path is not None and user_store is not None:
            save_settings(Database(backup_path), load_user_settings(user_store, user_id))
        if current_images.exists():
            image_backup = _image_backup_path(database, backup_path)
            current_images.replace(image_backup)
        # SQLite's backup API also handles an existing WAL safely. Replacing
        # just the .db file could leave the old journal beside the new data.
        _backup(staged_database, database.path)
        database_replaced = True
        staged_images.replace(current_images)
        images_replaced = True
        if user_store is not None:
            save_user_settings(user_store, user_id, imported_settings)
        return backup_path
    except Exception:
        if images_replaced:
            shutil.rmtree(current_images, ignore_errors=True)
        if image_backup is not None and image_backup.exists():
            image_backup.replace(current_images)
        if database_replaced:
            if backup_path is not None and backup_path.exists():
                _backup(backup_path, database.path)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
