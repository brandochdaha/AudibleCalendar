"""Repository named: local SQLite operations."""

from __future__ import annotations

from typing import Any
from ..author_sources import record_author_source
from ..db import Database
from ..normalization import is_followable_author
from .common import _json, _now
from .favourites import _clear_orphaned_favourites


def _upsert_named(connection: Any, table: str, item: dict[str, Any], marketplace: str, tracked: bool = False) -> int:
    original = item
    if table == "authors" and item.get("asin"):
        canonical = connection.execute(
            """SELECT a.asin, a.name FROM author_sources s JOIN authors a ON a.id = s.author_id
            WHERE s.marketplace = ? AND s.asin = ?""", (marketplace, str(item["asin"]))
        ).fetchone()
        if canonical and canonical["asin"] != str(item["asin"]):
            item = {**item, "asin": canonical["asin"], "name": canonical["name"]}
    author_id = _upsert_named_record(connection, table, item, marketplace, tracked)
    if table == "authors":
        record_author_source(connection, author_id, marketplace, original)
        if original.get("name") and original["name"].casefold() != item.get("name", "").casefold():
            connection.execute("INSERT OR IGNORE INTO author_aliases (author_id, alias_name) VALUES (?, ?)",
                               (author_id, original["name"]))
    return author_id


def _upsert_named_record(connection: Any, table: str, item: dict[str, Any], marketplace: str, tracked: bool = False) -> int:
    asin = str(item.get("asin") or "").strip()
    name = str(item.get("name") or "Unnamed").strip()
    if not asin:
        raise ValueError(f"{table} requires an ASIN")
    if table == "authors":
        existing = connection.execute(
            "SELECT id, asin, name, follow_all FROM authors WHERE marketplace = ? AND asin = ?",
            (marketplace, asin),
        ).fetchone()
        requested_follow_all = item.get("follow_all")
        follow_all = bool(requested_follow_all) if requested_follow_all is not None else bool(tracked and not existing)
        connection.execute(
            """
            INSERT INTO authors (marketplace, asin, name, description, image_url, audnexus_asin, is_tracked, follow_all, source_payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketplace, asin) DO UPDATE SET
                name=CASE WHEN authors.asin LIKE 'name:%' THEN excluded.name ELSE authors.name END,
                description=COALESCE(excluded.description, authors.description),
                image_url=COALESCE(NULLIF(excluded.image_url, ''), authors.image_url),
                audnexus_asin=COALESCE(excluded.audnexus_asin, authors.audnexus_asin),
                is_tracked=MAX(authors.is_tracked, excluded.is_tracked),
                follow_all=MAX(authors.follow_all, excluded.follow_all), source_payload=excluded.source_payload,
                updated_at=excluded.updated_at
            """,
            (
                marketplace,
                asin,
                name,
                item.get("description"),
                item.get("image_url") or item.get("image") or "",
                item.get("audnexus_asin"),
                int(tracked),
                int(follow_all),
                _json(item.get("payload") or item),
                _now(),
            ),
        )
        author_id = connection.execute(
            "SELECT id FROM authors WHERE marketplace = ? AND asin = ?", (marketplace, asin)
        ).fetchone()["id"]
        if existing and not str(existing["asin"]).startswith("name:") and existing["name"].casefold() != name.casefold():
            connection.execute(
                "INSERT OR IGNORE INTO author_aliases (author_id, alias_name) VALUES (?, ?)",
                (author_id, name),
            )
        return author_id
    else:
        connection.execute(
            """
            INSERT INTO series (marketplace, asin, name, is_verified, is_tracked, source_payload, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketplace, asin) DO UPDATE SET
                name=excluded.name, is_verified=MAX(series.is_verified, excluded.is_verified),
                is_tracked=MAX(series.is_tracked, excluded.is_tracked), source_payload=excluded.source_payload,
                updated_at=excluded.updated_at
            """,
            (
                marketplace,
                asin,
                name,
                int(item.get("is_verified", True)),
                int(tracked),
                _json(item.get("payload") or item),
                _now(),
            ),
        )
    return connection.execute(
        f"SELECT id FROM {table} WHERE marketplace = ? AND asin = ?", (marketplace, asin)
    ).fetchone()["id"]


def upsert_author(db: Database, item: dict[str, Any], marketplace: str, tracked: bool = False) -> int:
    with db.transaction() as connection:
        author_id = _upsert_named(
            connection,
            "authors",
            item,
            marketplace,
            tracked and is_followable_author(item),
        )
        stored = connection.execute(
            "SELECT name FROM authors WHERE id = ?",
            (author_id,),
        ).fetchone()
        if stored and not is_followable_author(stored):
            connection.execute(
                "UPDATE authors SET is_tracked = 0, follow_all = 0, is_favourite = 0, updated_at = ? WHERE id = ?",
                (_now(), author_id),
            )
            connection.execute("DELETE FROM author_series_preferences WHERE author_id = ?", (author_id,))
            connection.execute("DELETE FROM author_book_preferences WHERE author_id = ?", (author_id,))
            connection.execute("DELETE FROM book_authors WHERE author_id = ?", (author_id,))
            _clear_orphaned_favourites(connection)
        db.author_identities_dirty = True
        return author_id


def upsert_series(db: Database, item: dict[str, Any], marketplace: str, tracked: bool = False) -> int:
    with db.transaction() as connection:
        series_id = _upsert_named(connection, "series", item, marketplace, tracked)
        _clear_orphaned_favourites(connection, series_ids={series_id})
        return series_id
