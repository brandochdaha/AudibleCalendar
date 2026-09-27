"""Small SQLite wrapper and schema bootstrapper.

The application is intentionally local-first. A connection is opened per operation,
which keeps the code safe for FastAPI's request threads and the scheduler.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Iterator

from .author_sources import SOURCE_BACKFILL, SOURCE_SCHEMA
from .author_decisions import DECISION_SCHEMA
from .migrations import LATEST_SCHEMA_VERSION, SCHEMA


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.last_backup_path: Path | None = None
        self.identity_lock = RLock()
        self.author_identities_dirty = True

    def connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def compact(self) -> None:
        """Reclaim unused space after bulk deletion."""
        connection = self.connect()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
        finally:
            connection.close()

    def backup_to(self, destination_path: Path) -> None:
        """Copy a consistent snapshot, including any committed WAL contents."""
        source = self.connect()
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def _create_backup(
        self,
        connection: sqlite3.Connection,
        label: str,
    ) -> Path | None:
        """Create a consistent SQLite backup before a destructive migration."""

        if str(self.path) == ":memory:":
            return None
        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safe_label = "".join(character if character.isalnum() or character in "-_" else "-" for character in label)
        backup_path = backup_directory / f"{self.path.stem}.{safe_label}.{timestamp}.db"
        destination = sqlite3.connect(backup_path)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        self.last_backup_path = backup_path
        return backup_path

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, seed_demo: bool = False) -> None:
        connection = self.connect()
        try:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            has_schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone() is not None
            if has_schema and current_version in (6, 7, 8):
                self._create_backup(connection, "author-identities")
                try:
                    source_upgrade = SOURCE_SCHEMA + SOURCE_BACKFILL if current_version == 6 else ""
                    connection.executescript("BEGIN IMMEDIATE;\n" + source_upgrade + DECISION_SCHEMA +
                                             f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}; COMMIT;")
                except Exception:
                    connection.rollback()
                    raise
                current_version = LATEST_SCHEMA_VERSION
            if current_version not in (0, LATEST_SCHEMA_VERSION) or (has_schema and current_version != LATEST_SCHEMA_VERSION):
                raise RuntimeError(
                    f"Unsupported database schema {current_version}; this build requires {LATEST_SCHEMA_VERSION}."
                )
            if not has_schema:
                connection.executescript(SCHEMA)
                connection.execute(f"PRAGMA user_version = {LATEST_SCHEMA_VERSION}")
            if seed_demo:
                self._seed_demo(connection)
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _seed_demo(connection: sqlite3.Connection) -> None:
        existing = connection.execute("SELECT COUNT(*) AS count FROM books").fetchone()["count"]
        if existing:
            return

        today = date.today()
        author = connection.execute(
            """
            INSERT INTO authors (marketplace, asin, name, description, is_tracked, follow_all)
            VALUES (?, ?, ?, ?, 1, 1)
            """,
            ("demo", "DEMOAUTHOR01", "Example Author", "Demo data for a local AudibleCalendar preview."),
        )
        author_id = author.lastrowid
        series = connection.execute(
            """
            INSERT INTO series (marketplace, asin, name, is_tracked)
            VALUES (?, ?, ?, 1)
            """,
            ("demo", "DEMOSERIES01", "The Demo Archives"),
        )
        series_id = series.lastrowid

        books = [
            ("DEMOBOOK01", "The First Signal", today + timedelta(days=7), 1, "1"),
            ("DEMOBOOK02", "The Quiet Relay", today + timedelta(days=29), 1, "2"),
            ("DEMOBOOK03", "A Previous Transmission", today - timedelta(days=45), 0, "0"),
        ]
        for asin, title, release_date, tracked, sequence in books:
            book = connection.execute(
                """
                INSERT INTO books (
                    marketplace, asin, title, description, release_date,
                    image_url, audible_url, is_preorder, is_tracked, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    "demo",
                    asin,
                    title,
                    "A local demo audiobook entry. Replace demo data with real catalog entries.",
                    release_date.isoformat(),
                    "",
                    "https://www.audible.com/",
                    tracked,
                    tracked,
                ),
            )
            book_id = book.lastrowid
            connection.execute(
                "INSERT INTO book_authors (book_id, author_id, role) VALUES (?, ?, ?)",
                (book_id, author_id, "Author"),
            )
            connection.execute(
                """
                INSERT INTO book_series (book_id, series_id, sequence_text, sequence_number)
                VALUES (?, ?, ?, ?)
                """,
                (book_id, series_id, sequence, float(sequence)),
            )
