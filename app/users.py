"""Account registry, sessions, and per-user database provisioning."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import DEFAULT_PASSWORD, DEFAULT_USERNAME, hash_password, verify_password, validate_new_password


SESSION_TTL = timedelta(days=30)


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    role: str
    active: bool
    password_is_default: bool
    data_path: Path

    @property
    def is_admin(self) -> bool:
        return self.id == 1 and self.role == "admin"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.id,
            "username": self.username,
            "role": self.role,
            "active": self.active,
            "password_is_default": self.password_is_default,
            "is_admin": self.is_admin,
        }


class UserStore:
    """Store global account metadata separately from each user's data DB."""

    def __init__(self, database_path: Path, *, demo_data: bool = False):
        self.database_path = database_path
        if str(database_path) == ":memory:":
            raise ValueError("Multi-user mode requires a file-backed database path")
        self.registry_path = database_path.with_name(f"{database_path.stem}-users.db")
        self.data_root = database_path.parent / f"{database_path.stem}-users"
        self.demo_data = demo_data
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.registry_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> UserRecord:
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            active=bool(row["active"]),
            password_is_default=bool(row["password_is_default"]),
            data_path=Path(row["data_path"]),
        )

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_is_default INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin', 'user')),
                    active INTEGER NOT NULL DEFAULT 1,
                    data_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, setting_key)
                );
                CREATE TABLE IF NOT EXISTS instance_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                );
                """
            )
            # Repair older releases that allowed the bootstrap warning to be
            # cleared while retaining the publicly known password.
            if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
                for row in connection.execute("SELECT id, password_hash, password_salt FROM users WHERE password_is_default = 0").fetchall():
                    if verify_password(DEFAULT_PASSWORD, row["password_hash"], row["password_salt"]):
                        connection.execute("UPDATE users SET password_is_default = 1 WHERE id = ?", (row["id"],))
                connection.execute("PRAGMA user_version = 1")
            # The bootstrap account is the sole administrator, including for
            # installations created when secondary administrators were allowed.
            connection.execute("""
                DELETE FROM sessions WHERE user_id IN (
                    SELECT id FROM users
                    WHERE role != CASE WHEN id = 1 THEN 'admin' ELSE 'user' END
                       OR (id = 1 AND active = 0)
                )
            """)
            connection.execute("""
                UPDATE users
                SET role = CASE WHEN id = 1 THEN 'admin' ELSE 'user' END,
                    active = CASE WHEN id = 1 THEN 1 ELSE active END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE role != CASE WHEN id = 1 THEN 'admin' ELSE 'user' END
                   OR (id = 1 AND active = 0)
            """)
            connection.commit()
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return
        finally:
            connection.close()

        if self.database_path.exists():
            raise RuntimeError("Single-user database migration is no longer supported; use a current-format archive.")
        primary_path = self.data_root / "1" / "audiblecalendar.db"
        username = DEFAULT_USERNAME
        password_hash, password_salt = hash_password(DEFAULT_PASSWORD)
        password_is_default = True

        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, password_salt,
                    password_is_default, role, active, data_path
                ) VALUES (1, ?, ?, ?, ?, 'admin', 1, ?)
                """,
                (
                    username,
                    password_hash,
                    password_salt,
                    int(password_is_default),
                    str(primary_path),
                ),
            )
            # Existing registries never receive this marker on upgrade.
            connection.execute(
                "INSERT INTO instance_settings (setting_key, setting_value) VALUES ('browser_time_zone_pending', 'true')"
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, user_id: int) -> UserRecord | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            connection.close()
        return self._row_to_user(row) if row else None

    def browser_time_zone_pending(self, user: UserRecord) -> bool:
        if user.id != 1 or not user.password_is_default:
            return False
        with self.transaction() as connection:
            return connection.execute(
                "SELECT 1 FROM instance_settings WHERE setting_key = 'browser_time_zone_pending' AND setting_value = 'true'"
            ).fetchone() is not None


    def list(self, *, include_inactive: bool = True) -> list[UserRecord]:
        connection = self._connect()
        try:
            query = "SELECT * FROM users"
            if not include_inactive:
                query += " WHERE active = 1"
            rows = connection.execute(query + " ORDER BY id").fetchall()
        finally:
            connection.close()
        return [self._row_to_user(row) for row in rows]

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE AND active = 1",
                (username.strip(),),
            ).fetchone()
        finally:
            connection.close()
        if not row or not verify_password(password, row["password_hash"], row["password_salt"]):
            return None
        return self._row_to_user(row)

    def create(self, username: str, password: str, *, role: str = "user") -> UserRecord:
        clean_username = username.strip()
        if not clean_username:
            raise ValueError("Username cannot be empty")
        validate_new_password(password)
        if role != "user":
            raise ValueError("Only the initial account can be an administrator")
        password_hash, password_salt = hash_password(password)
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username, password_hash, password_salt,
                    password_is_default, role, active, data_path
                ) VALUES (?, ?, ?, 0, ?, 1, '')
                """,
                (clean_username, password_hash, password_salt, role),
            )
            user_id = int(cursor.lastrowid)
            data_path = self.data_root / str(user_id) / "audiblecalendar.db"
            connection.execute(
                "UPDATE users SET data_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(data_path), user_id),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ValueError("That username is already in use") from exc
        finally:
            connection.close()
        return self.get(user_id)  # type: ignore[return-value]

    def update(
        self,
        user_id: int,
        *,
        username: str | None = None,
        password: str | None = None,
        active: bool | None = None,
        role: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> UserRecord:
        current = self.get(user_id)
        if current is None:
            raise ValueError("User not found")
        clean_username = current.username if username is None else username.strip()
        if not clean_username:
            raise ValueError("Username cannot be empty")
        if password is not None:
            validate_new_password(password)
        next_role = "admin" if user_id == 1 else "user"
        if role is not None and role != next_role:
            raise ValueError("Only the initial account can be an administrator; account roles cannot be changed")
        if user_id == 1 and active is False:
            raise ValueError("The initial administrator account cannot be disabled")
        password_hash, password_salt = (None, None)
        if password is not None:
            if current.password_is_default:
                with self.transaction() as check:
                    row = check.execute("SELECT password_hash, password_salt FROM users WHERE id = ?", (user_id,)).fetchone()
                if verify_password(password, row["password_hash"], row["password_salt"]):
                    raise ValueError("Choose a different password to finish account setup")
            password_hash, password_salt = hash_password(password)
        assignments = ["username = ?", "role = ?", "updated_at = CURRENT_TIMESTAMP"]
        parameters: list[Any] = [clean_username, next_role]
        if password_hash is not None and password_salt is not None:
            assignments.extend(["password_hash = ?", "password_salt = ?", "password_is_default = 0"])
            parameters.extend([password_hash, password_salt])
        if active is not None:
            assignments.append("active = ?")
            parameters.append(int(active))
        parameters.append(user_id)
        owned = connection is None
        connection = connection if connection is not None else self._connect()
        try:
            connection.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", parameters)
            if user_id == 1 and password is not None:
                connection.execute("DELETE FROM instance_settings WHERE setting_key = 'browser_time_zone_pending'")
            if clean_username != current.username or password is not None or active is False:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if owned:
                connection.commit()
        except sqlite3.IntegrityError as exc:
            if owned:
                connection.rollback()
            raise ValueError("That username is already in use") from exc
        finally:
            if owned:
                connection.close()
        return self._row_to_user(row)

    def issue_session(self, user_id: int, now: datetime | None = None) -> str:
        token = secrets.token_urlsafe(48)
        issued_at = now or datetime.now(timezone.utc)
        expires_at = issued_at + SESSION_TTL
        connection = self._connect()
        try:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (issued_at.isoformat(),))
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (self._token_hash(token), user_id, expires_at.isoformat()),
            )
            connection.commit()
        finally:
            connection.close()
        return token

    def session_user(self, token: str | None, now: datetime | None = None) -> UserRecord | None:
        if not token:
            return None
        current_time = now or datetime.now(timezone.utc)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND users.active = 1
                  AND sessions.expires_at > ?
                """,
                (self._token_hash(token), current_time.isoformat()),
            ).fetchone()
        finally:
            connection.close()
        return self._row_to_user(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        connection = self._connect()
        try:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
