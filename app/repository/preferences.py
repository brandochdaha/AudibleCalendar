"""Repository preferences: local SQLite operations."""

from __future__ import annotations

from typing import Any
from ..db import Database
from .common import _decode, _now, _series_favourite_sql
from .favourites import _clear_orphaned_favourites
from .relations import _book_with_relations
from .scopes import _tracked_author_scopes


def list_author_series_preferences(db: Database, author_id: int) -> list[dict[str, Any]]:
    with db.transaction() as connection:
        rows = connection.execute(
            """
            SELECT asp.*, s.asin AS series_asin, s.name AS series_name
            FROM author_series_preferences asp
            JOIN series s ON s.id = asp.series_id
            WHERE asp.author_id = ?
            ORDER BY s.name
            """,
            (author_id,),
        ).fetchall()
        return [_decode(row) for row in rows]


def set_author_series_preference(
    db: Database,
    author_id: int,
    series_id: int,
    selected: bool,
    *,
    preserve_follow_all: bool = False,
) -> None:
    with db.transaction() as connection:
        author = connection.execute(
            "SELECT follow_all FROM authors WHERE id = ?",
            (author_id,),
        ).fetchone()
        if not (preserve_follow_all and author and author["follow_all"]):
            connection.execute(
                "UPDATE authors SET follow_all = 0, updated_at = ? WHERE id = ?",
                (_now(), author_id),
            )
        connection.execute(
            """
            INSERT INTO author_series_preferences (author_id, series_id, is_selected, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(author_id, series_id) DO UPDATE SET
                is_selected=excluded.is_selected,
                updated_at=excluded.updated_at
            """,
            (author_id, series_id, int(selected), _now()),
        )
        _clear_orphaned_favourites(connection, series_ids={series_id})


def list_author_book_preferences(db: Database, author_id: int) -> list[dict[str, Any]]:
    with db.transaction() as connection:
        rows = connection.execute(
            """
            SELECT abp.*, b.asin AS book_asin, b.title AS book_title,
                   b.image_url, b.release_date
            FROM author_book_preferences abp
            JOIN books b ON b.id = abp.book_id
            WHERE abp.author_id = ?
            ORDER BY b.title
            """,
            (author_id,),
        ).fetchall()
        return [_decode(row) for row in rows]


def set_author_book_preference(
    db: Database,
    author_id: int,
    book_id: int,
    selected: bool,
    *,
    preserve_follow_all: bool = False,
) -> None:
    with db.transaction() as connection:
        author = connection.execute(
            "SELECT follow_all FROM authors WHERE id = ?",
            (author_id,),
        ).fetchone()
        if not (preserve_follow_all and author and author["follow_all"]):
            connection.execute(
                "UPDATE authors SET follow_all = 0, updated_at = ? WHERE id = ?",
                (_now(), author_id),
            )
        connection.execute(
            """
            INSERT INTO author_book_preferences (author_id, book_id, is_selected, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(author_id, book_id) DO UPDATE SET
                is_selected=excluded.is_selected,
                updated_at=excluded.updated_at
            """,
            (author_id, book_id, int(selected), _now()),
        )
        _clear_orphaned_favourites(connection, book_ids={book_id})


def set_author_follow_all(db: Database, author_id: int, follow_all: bool) -> None:
    """Persist the author's All toggle independently of child rows."""

    with db.transaction() as connection:
        connection.execute(
            "UPDATE authors SET is_tracked = 1, follow_all = ?, updated_at = ? WHERE id = ?",
            (int(follow_all), _now(), author_id),
        )
        if follow_all:
            # Turning All on is an explicit override of older exclusions.
            connection.execute("DELETE FROM author_series_preferences WHERE author_id = ?", (author_id,))
            connection.execute("DELETE FROM author_book_preferences WHERE author_id = ?", (author_id,))
        else:
            _clear_orphaned_favourites(connection)


def clear_author_release_selections(db: Database, author_id: int) -> None:
    """Keep an author followed while clearing every selected release."""

    with db.transaction() as connection:
        connection.execute(
            "UPDATE authors SET is_tracked = 1, follow_all = 0, updated_at = ? WHERE id = ?",
            (_now(), author_id),
        )
        connection.execute("DELETE FROM author_series_preferences WHERE author_id = ?", (author_id,))
        connection.execute("DELETE FROM author_book_preferences WHERE author_id = ?", (author_id,))
        _clear_orphaned_favourites(connection)


def list_selected_author_book_ids(db: Database) -> set[int]:
    """Return standalone-book sync targets without expanding their relations."""

    with db.transaction() as connection:
        return {
            row["book_id"]
            for row in connection.execute(
                """
                SELECT DISTINCT b.id AS book_id
                FROM books b
                JOIN author_book_preferences abp ON abp.book_id = b.id
                JOIN authors a ON a.id = abp.author_id
                LEFT JOIN book_series bs ON bs.book_id = b.id
                WHERE abp.is_selected = 1
                  AND a.is_tracked = 1
                  AND bs.book_id IS NULL
                """
            ).fetchall()
        }


def list_author_followed_scopes(db: Database, author_id: int) -> dict[str, list[dict[str, Any]]]:
    """Return the child chips represented beneath one followed author."""

    with db.transaction() as connection:
        series_ids, book_ids = _tracked_author_scopes(connection, author_id)
        series: list[dict[str, Any]] = []
        books: list[dict[str, Any]] = []
        if series_ids:
            placeholders = ", ".join("?" for _ in series_ids)
            series = [
                _decode(row)
                for row in connection.execute(
                    f"""
                    SELECT s.*, {_series_favourite_sql("s")} AS effective_is_favourite
                    FROM series s
                    WHERE s.id IN ({placeholders})
                    ORDER BY s.name
                    """,
                    tuple(series_ids),
                ).fetchall()
            ]
            for item in series:
                item["is_favourite"] = bool(
                    item.pop("effective_is_favourite", item.get("is_favourite"))
                )
        if book_ids:
            placeholders = ", ".join("?" for _ in book_ids)
            books = [
                _book_with_relations(connection, row)
                for row in connection.execute(
                    f"SELECT * FROM books WHERE id IN ({placeholders}) ORDER BY title",
                    tuple(book_ids),
                ).fetchall()
            ]
        return {"series": series, "books": books}


def author_has_scope_overrides(db: Database, author_id: int) -> bool:
    """Whether an author has an explicit child inclusion or exclusion."""

    with db.transaction() as connection:
        return bool(
            connection.execute(
                """
                SELECT 1
                FROM author_series_preferences
                WHERE author_id = ?
                UNION ALL
                SELECT 1
                FROM author_book_preferences
                WHERE author_id = ?
                LIMIT 1
                """,
                (author_id, author_id),
            ).fetchone()
        )


def _upsert_series_selection(
    connection: Any,
    author_id: int,
    series_id: int,
    selected: bool,
    *,
    preserve_follow_all: bool = False,
) -> None:
    if not preserve_follow_all:
        connection.execute(
            "UPDATE authors SET follow_all = 0, updated_at = ? WHERE id = ?",
            (_now(), author_id),
        )
    connection.execute(
        """
        INSERT INTO author_series_preferences (author_id, series_id, is_selected, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(author_id, series_id) DO UPDATE SET
            is_selected=excluded.is_selected,
            updated_at=excluded.updated_at
        """,
        (author_id, series_id, int(selected), _now()),
    )


def _upsert_book_selection(
    connection: Any,
    author_id: int,
    book_id: int,
    selected: bool,
    *,
    preserve_follow_all: bool = False,
) -> None:
    if not preserve_follow_all:
        connection.execute(
            "UPDATE authors SET follow_all = 0, updated_at = ? WHERE id = ?",
            (_now(), author_id),
        )
    connection.execute(
        """
        INSERT INTO author_book_preferences (author_id, book_id, is_selected, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(author_id, book_id) DO UPDATE SET
            is_selected=excluded.is_selected,
            updated_at=excluded.updated_at
        """,
        (author_id, book_id, int(selected), _now()),
    )
