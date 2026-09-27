"""Repository books: local SQLite operations."""

from __future__ import annotations

from typing import Any
from ..author_sources import record_author_book
from ..db import Database
from ..normalization import is_followable_author
from .common import _json, _now
from .favourites import _clear_orphaned_favourites
from .named import _upsert_named
from .preferences import _upsert_series_selection


def _reconcile_followed_book(connection: Any, book_id: int) -> None:
    """Promote standalone follows to series while preserving their ownership."""

    book = connection.execute(
        "SELECT is_tracked FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    series_rows = connection.execute(
        "SELECT series_id FROM book_series WHERE book_id = ?", (book_id,)
    ).fetchall()
    if not book or not series_rows:
        return
    now = _now()
    connection.execute(
        "UPDATE books SET is_favourite = 0, favourite_override = NULL, updated_at = ? WHERE id = ?",
        (now, book_id),
    )
    if book["is_tracked"]:
        for series in series_rows:
            connection.execute(
                "UPDATE series SET is_tracked = 1, updated_at = ? WHERE id = ?",
                (now, series["series_id"]),
            )
        connection.execute(
            "UPDATE books SET is_tracked = 0, updated_at = ? WHERE id = ?",
            (now, book_id),
        )

    # Only the author who selected this standalone inherits its series.
    # Other authors and direct follows cannot recreate cleared selections.
    selections = connection.execute(
        "SELECT author_id FROM author_book_preferences WHERE book_id = ? AND is_selected = 1",
        (book_id,),
    ).fetchall()
    for selection in selections:
        for series in series_rows:
            existing = connection.execute(
                "SELECT 1 FROM author_series_preferences WHERE author_id = ? AND series_id = ?",
                (selection["author_id"], series["series_id"]),
            ).fetchone()
            if not existing:
                _upsert_series_selection(
                    connection, selection["author_id"], series["series_id"], True,
                    preserve_follow_all=True,
                )
        connection.execute(
            "DELETE FROM author_book_preferences WHERE author_id = ? AND book_id = ?",
            (selection["author_id"], book_id),
        )


def attach_missing_book_series(db: Database, book_id: int, items: list[dict[str, Any]]) -> bool:
    """Attach locally inferred, known series without rewriting source metadata."""

    with db.transaction() as connection:
        connection.execute("BEGIN IMMEDIATE")
        book = connection.execute(
            "SELECT marketplace FROM books WHERE id = ?", (book_id,),
        ).fetchone()
        if not book or connection.execute(
            "SELECT 1 FROM book_series WHERE book_id = ? LIMIT 1", (book_id,),
        ).fetchone():
            return False
        series_ids: set[int] = set()
        for item in items:
            series = connection.execute(
                "SELECT id FROM series WHERE marketplace = ? AND asin = ?",
                (book["marketplace"], item.get("asin")),
            ).fetchone()
            if series is None:
                continue
            connection.execute(
                "INSERT INTO book_series (book_id, series_id, sequence_text, sequence_number) "
                "VALUES (?, ?, ?, ?)",
                (book_id, series["id"], item.get("sequence"), item.get("sequence_number")),
            )
            series_ids.add(series["id"])
        if not series_ids:
            return False
        _reconcile_followed_book(connection, book_id)
        _clear_orphaned_favourites(connection, series_ids=series_ids, book_ids={book_id})
        db.author_identities_dirty = True
        return True


def upsert_book(db: Database, item: dict[str, Any], marketplace: str, tracked: bool = False) -> int:
    asin = str(item.get("asin") or "").strip()
    if not asin:
        raise ValueError("book requires an ASIN")
    with db.transaction() as connection:
        current = connection.execute(
            "SELECT id, release_date FROM books WHERE marketplace = ? AND asin = ?",
            (marketplace, asin),
        ).fetchone()
        new_release_date = item.get("release_date")
        connection.execute(
            """
            INSERT INTO books (
                marketplace, asin, title, subtitle, description, release_date, release_date_precision,
                image_url, audible_url, publisher, runtime_minutes, language, is_preorder,
                is_available, is_tracked, source_payload, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketplace, asin) DO UPDATE SET
                title=excluded.title, subtitle=COALESCE(excluded.subtitle, books.subtitle),
                description=COALESCE(excluded.description, books.description),
                release_date=COALESCE(excluded.release_date, books.release_date),
                release_date_precision=excluded.release_date_precision,
                image_url=COALESCE(NULLIF(excluded.image_url, ''), books.image_url),
                audible_url=COALESCE(NULLIF(excluded.audible_url, ''), books.audible_url),
                publisher=COALESCE(excluded.publisher, books.publisher), runtime_minutes=COALESCE(excluded.runtime_minutes, books.runtime_minutes),
                language=COALESCE(excluded.language, books.language), is_preorder=excluded.is_preorder,
                is_available=COALESCE(excluded.is_available, books.is_available),
                is_tracked=MAX(books.is_tracked, excluded.is_tracked), source_payload=excluded.source_payload,
                last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at
            """,
            (
                marketplace,
                asin,
                item.get("title") or "Untitled audiobook",
                item.get("subtitle"),
                item.get("description"),
                new_release_date,
                item.get("release_date_precision") or "unknown",
                item.get("image_url") or "",
                item.get("audible_url") or "",
                item.get("publisher"),
                item.get("runtime_minutes"),
                item.get("language"),
                int(bool(item.get("is_preorder"))),
                item.get("is_available"),
                int(tracked),
                _json(item.get("source_payload") or item),
                _now(),
                _now(),
            ),
        )
        book_id = connection.execute(
            "SELECT id FROM books WHERE marketplace = ? AND asin = ?", (marketplace, asin)
        ).fetchone()["id"]
        old_release_date = current["release_date"] if current else None
        if old_release_date and new_release_date and old_release_date != new_release_date:
            connection.execute(
                "INSERT INTO release_date_history (book_id, old_release_date, new_release_date, source) VALUES (?, ?, ?, ?)",
                (book_id, old_release_date, new_release_date, "audible"),
            )
        for author in item.get("authors", []):
            if not author.get("asin"):
                continue
            if not is_followable_author(author):
                connection.execute(
                    """
                    DELETE FROM book_authors
                    WHERE book_id = ? AND author_id IN (
                        SELECT id FROM authors WHERE marketplace = ? AND asin = ?
                    )
                    """,
                    (book_id, marketplace, str(author.get("asin"))),
                )
                continue
            author_id = _upsert_named(connection, "authors", author, marketplace)
            record_author_book(connection, marketplace, str(author["asin"]), book_id)
            connection.execute(
                "INSERT OR REPLACE INTO book_authors (book_id, author_id, role) VALUES (?, ?, ?)",
                (book_id, author_id, author.get("role")),
            )
        for series in item.get("series", []):
            if not series.get("asin"):
                continue
            series_id = _upsert_named(connection, "series", series, marketplace)
            connection.execute(
                """
                INSERT OR REPLACE INTO book_series (book_id, series_id, sequence_text, sequence_number)
                VALUES (?, ?, ?, ?)
                """,
                (book_id, series_id, series.get("sequence"), series.get("sequence_number")),
            )
        _reconcile_followed_book(connection, book_id)
        series_ids = {
            row["series_id"]
            for row in connection.execute(
                "SELECT series_id FROM book_series WHERE book_id = ?", (book_id,)
            ).fetchall()
        }
        _clear_orphaned_favourites(connection, series_ids=series_ids, book_ids={book_id})
        db.author_identities_dirty = True
        return book_id
