"""Account notification settings, durable batches and per-book delivery history."""

import json
from datetime import timedelta

from .notification_settings import CHANNELS, NotificationSettings, validate_destination, web_url


SCHEMA = """
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings TEXT NOT NULL,
    enabled_since TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    digest_day TEXT,
    payload TEXT,
    retry_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT,
    UNIQUE (user_id, region, channel, digest_day)
);
CREATE TABLE IF NOT EXISTS notification_books (
    user_id INTEGER NOT NULL,
    region TEXT NOT NULL,
    channel TEXT NOT NULL,
    asin TEXT NOT NULL,
    batch_id INTEGER NOT NULL REFERENCES notification_batches(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, region, channel, asin)
);
CREATE INDEX IF NOT EXISTS notification_pending
    ON notification_batches (user_id, region, retry_at) WHERE sent_at IS NULL;
"""


def load_preferences(connection, user_id):
    row = connection.execute(
        "SELECT settings, enabled_since FROM notification_preferences WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return NotificationSettings(), {}
    return NotificationSettings.model_validate_json(row["settings"]), json.loads(row["enabled_since"])


def public_preferences(user_store, user_id):
    with user_store.transaction() as connection:
        settings, _ = load_preferences(connection, user_id)
        errors = {
            row["channel"]: row["error"] for row in connection.execute("""
                SELECT DISTINCT channel, error FROM notification_batches
                WHERE user_id = ? AND sent_at IS NULL AND error IS NOT NULL ORDER BY id
            """, (user_id,))
        }
    values = settings.model_dump(exclude={channel: {"secret"} for channel in CHANNELS})
    for channel in CHANNELS:
        values[channel]["has_secret"] = bool(getattr(settings, channel).secret)
        values[channel]["error"] = errors.get(channel)
    return values


def save_preferences(connection, user, settings, today):
    previous, enabled_since = load_preferences(connection, user.id)
    if settings.calendar_url:
        settings.calendar_url = web_url(settings.calendar_url)
    values = settings.model_dump()
    for channel in CHANNELS:
        destination = getattr(settings, channel)
        if "secret" not in destination.model_fields_set:
            destination.secret = getattr(previous, channel).secret
        values[channel]["secret"] = destination.secret.get_secret_value() if destination.secret else None
        if destination.enabled:
            if settings.enabled:
                validate_destination(channel, destination, admin=user.is_admin)
                enabled_since.setdefault(channel, today.isoformat())
        else:
            enabled_since.pop(channel, None)
            connection.execute("""
                DELETE FROM notification_batches WHERE user_id = ? AND channel = ? AND sent_at IS NULL
            """, (user.id, channel))
    connection.execute("""
        INSERT INTO notification_preferences (user_id, settings, enabled_since) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET settings = excluded.settings, enabled_since = excluded.enabled_since
    """, (user.id, json.dumps(values), json.dumps(enabled_since)))


def queue_releases(connection, key, *, books, settings, since, day, now, digest_key=None):
    digest = settings.mode == "digest"
    today = day.isoformat()
    yesterday = (day - timedelta(days=1)).isoformat()
    digest_days = {
        row[0] for row in connection.execute("""
            SELECT digest_day FROM notification_batches
            WHERE user_id = ? AND region = ? AND channel = ? AND digest_day IN (?, ?)
        """, (*key, digest_key or today, yesterday))
    }
    if digest and (digest_key or today) in digest_days:
        return
    known = {row[0] for row in connection.execute("""
        SELECT asin FROM notification_books WHERE user_id = ? AND region = ? AND channel = ?
    """, key)}
    eligible = [
        {**book, "missed_digest": digest and yesterday in digest_days and book["release_date"] == yesterday}
        for book in books if book["release_date"] >= since and book["asin"] not in known
    ]
    if not eligible:
        return
    for group in ([eligible] if digest else [[book] for book in eligible]):
        batch = connection.execute("""
            INSERT INTO notification_batches (user_id, region, channel, digest_day, payload, retry_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (*key, (digest_key or today) if digest else None, json.dumps(group), now.isoformat())).lastrowid
        connection.executemany("""
            INSERT INTO notification_books (user_id, region, channel, asin, batch_id)
            VALUES (?, ?, ?, ?, ?)
        """, ((*key, book["asin"], batch) for book in group))


def claim_due(user_store, user_id, region, now):
    with user_store.transaction() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute("""
            SELECT id, channel, digest_day, payload FROM notification_batches
            WHERE user_id = ? AND region = ? AND sent_at IS NULL AND retry_at <= ?
            ORDER BY id LIMIT 1
        """, (user_id, region, now.isoformat())).fetchone()
        if batch:
            connection.execute("UPDATE notification_batches SET retry_at = ? WHERE id = ?",
                               ((now + timedelta(minutes=5)).isoformat(), batch["id"]))
        return batch


def finish_delivery(user_store, batch_id, now, error):
    with user_store.transaction() as connection:
        if error:
            retry = (now + timedelta(minutes=5)).isoformat()
            connection.execute("""
                UPDATE notification_batches
                SET retry_at = MAX(retry_at, ?), error = CASE WHEN id = ? THEN ? ELSE error END
                WHERE (user_id, region, channel) = (
                    SELECT user_id, region, channel FROM notification_batches WHERE id = ?
                ) AND sent_at IS NULL
            """, (retry, batch_id, error, batch_id))
        else:
            connection.execute("""
                UPDATE notification_batches SET sent_at = ?, payload = NULL, error = NULL WHERE id = ?
            """, (now.isoformat(), batch_id))
