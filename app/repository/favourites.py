"""Repository favourites: local SQLite operations."""

from __future__ import annotations

from ..db import Database
from .common import _followed_book_scope_sql, _now, _series_followed_sql


def _clear_orphaned_favourites(
    connection,
    *,
    series_ids: set[int] | None = None,
    book_ids: set[int] | None = None,
) -> None:
    """Clear stored favourite intent only after its last follow source is gone."""

    now = _now()
    if series_ids is None:
        series_where = "1 = 1"
        series_parameters: tuple = ()
    else:
        if not series_ids:
            series_where = None
        else:
            placeholders = ", ".join("?" for _ in series_ids)
            series_where = f"id IN ({placeholders})"
            series_parameters = tuple(series_ids)
    if series_where:
        connection.execute(
            f"""
            UPDATE series
            SET is_favourite = 0, favourite_override = NULL, updated_at = ?
            WHERE {series_where}
              AND NOT {_series_followed_sql("series")}
            """,
            (now, *series_parameters),
        )

    if book_ids is None:
        book_where = "1 = 1"
        book_parameters: tuple = ()
    else:
        if not book_ids:
            book_where = None
        else:
            placeholders = ", ".join("?" for _ in book_ids)
            book_where = f"id IN ({placeholders})"
            book_parameters = tuple(book_ids)
    if book_where:
        connection.execute(
            f"""
            UPDATE books
            SET is_favourite = 0, favourite_override = NULL, updated_at = ?
            WHERE {book_where}
              AND NOT {_followed_book_scope_sql("books")}
            """,
            (now, *book_parameters),
        )


def set_author_favourite(db: Database, author_id: int, favourite: bool) -> None:
    """Set the optional favourite flag; favourites always belong to followed authors."""

    with db.transaction() as connection:
        updated = connection.execute(
            """
            UPDATE authors
            SET is_favourite = CASE WHEN ? = 1 AND is_tracked = 1 THEN 1 ELSE 0 END,
                updated_at = ?
            WHERE id = ?
            """,
            (int(favourite), _now(), author_id),
        ).rowcount
        if not updated:
            raise ValueError("Author not found")


def set_series_favourite(db: Database, series_id: int, favourite: bool) -> None:
    """Set an explicit favourite flag without changing the series follow."""

    with db.transaction() as connection:
        series = connection.execute(
            "SELECT id FROM series WHERE id = ?",
            (series_id,),
        ).fetchone()
        if not series:
            raise ValueError("Series not found")
        if favourite and not connection.execute(
            f"""
            SELECT 1
            FROM series
            WHERE id = ?
              AND {_series_followed_sql("series")}
            """,
            (series_id,),
        ).fetchone():
            raise ValueError("Series must be followed before it can be favorited")
        connection.execute(
            """
            UPDATE series
            SET is_favourite = ?, favourite_override = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(favourite), int(favourite), _now(), series_id),
        )
        _clear_orphaned_favourites(connection, series_ids={series_id})


def set_book_favourite(db: Database, book_id: int, favourite: bool) -> None:
    """Set an explicit favourite flag for a followed standalone book."""

    with db.transaction() as connection:
        book = connection.execute(
            "SELECT id FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        if not book:
            raise ValueError("Book not found")
        if connection.execute(
            "SELECT 1 FROM book_series WHERE book_id = ? LIMIT 1",
            (book_id,),
        ).fetchone():
            if favourite:
                raise ValueError("Books in a series must be favorited through their series")
            connection.execute(
                "UPDATE books SET is_favourite = 0, favourite_override = NULL, updated_at = ? WHERE id = ?",
                (_now(), book_id),
            )
            _clear_orphaned_favourites(connection, book_ids={book_id})
            return
        if favourite and not connection.execute(
            f"""
            SELECT 1
            FROM books b
            WHERE b.id = ?
              AND (
                  b.is_tracked = 1
                  OR EXISTS (
                      SELECT 1
                      FROM book_authors ba
                      JOIN authors a ON a.id = ba.author_id
                      WHERE ba.book_id = b.id
                        AND a.is_tracked = 1
                        AND (
                            (a.follow_all = 1 AND NOT EXISTS (
                                SELECT 1 FROM author_book_preferences excluded_abp
                                WHERE excluded_abp.author_id = a.id
                                  AND excluded_abp.book_id = b.id
                                  AND excluded_abp.is_selected = 0
                            ))
                            OR EXISTS (
                                SELECT 1 FROM author_book_preferences selected_abp
                                WHERE selected_abp.author_id = a.id
                                  AND selected_abp.book_id = b.id
                                  AND selected_abp.is_selected = 1
                            )
                        )
                  )
              )
            """,
            (book_id,),
        ).fetchone():
            raise ValueError("Book must be followed before it can be favorited")
        connection.execute(
            """
            UPDATE books
            SET is_favourite = ?, favourite_override = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(favourite), int(favourite), _now(), book_id),
        )
        _clear_orphaned_favourites(connection, book_ids={book_id})
