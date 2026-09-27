"""Repository scopes: local SQLite operations."""

from __future__ import annotations

from typing import Any
from ..author_identity import author_display_names
from ..normalization import is_followable_author
from .common import _decode


def _known_author_series(connection: Any, author_id: int) -> list[int]:
    return [
        row["series_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT bs.series_id
            FROM book_authors ba
            JOIN book_series bs ON bs.book_id = ba.book_id
            WHERE ba.author_id = ?
            UNION
            SELECT series_id FROM author_series_preferences WHERE author_id = ?
            """,
            (author_id, author_id),
        ).fetchall()
    ]


def _known_author_standalone_books(connection: Any, author_id: int) -> list[int]:
    return [
        row["book_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT b.id AS book_id
            FROM books b
            JOIN book_authors ba ON ba.book_id = b.id
            LEFT JOIN book_series bs ON bs.book_id = b.id
            WHERE ba.author_id = ? AND bs.book_id IS NULL
            UNION
            SELECT abp.book_id
            FROM author_book_preferences abp
            LEFT JOIN book_series bs ON bs.book_id = abp.book_id
            WHERE abp.author_id = ? AND bs.book_id IS NULL
            """,
            (author_id, author_id),
        ).fetchall()
    ]


def _tracked_author_scopes(connection: Any, author_id: int) -> tuple[list[int], list[int]]:
    """Return the effective series and standalone-book scopes for an author."""

    author = connection.execute(
        "SELECT is_tracked, follow_all FROM authors WHERE id = ?",
        (author_id,),
    ).fetchone()
    if not author or not author["is_tracked"]:
        return [], []
    known_series = _known_author_series(connection, author_id)
    known_books = _known_author_standalone_books(connection, author_id)
    if author["follow_all"]:
        excluded_series = {
            row["series_id"]
            for row in connection.execute(
                """
                SELECT series_id FROM author_series_preferences
                WHERE author_id = ? AND is_selected = 0
                """,
                (author_id,),
            ).fetchall()
        }
        excluded_books = {
            row["book_id"]
            for row in connection.execute(
                """
                SELECT book_id FROM author_book_preferences
                WHERE author_id = ? AND is_selected = 0
                """,
                (author_id,),
            ).fetchall()
        }
        return (
            [series_id for series_id in known_series if series_id not in excluded_series],
            [book_id for book_id in known_books if book_id not in excluded_books],
        )
    series = [
        row["series_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT s.id AS series_id
            FROM series s
            LEFT JOIN author_series_preferences asp
              ON asp.series_id = s.id AND asp.author_id = ?
            WHERE asp.is_selected = 1
              AND (
                  asp.author_id IS NOT NULL
                  OR EXISTS (
                      SELECT 1 FROM book_series bs
                      JOIN book_authors ba ON ba.book_id = bs.book_id
                      WHERE bs.series_id = s.id AND ba.author_id = ?
                  )
              )
            """,
            (author_id, author_id),
        ).fetchall()
    ]
    books = [
        row["book_id"]
        for row in connection.execute(
            """
            SELECT DISTINCT b.id AS book_id
            FROM books b
            LEFT JOIN author_book_preferences abp
              ON abp.book_id = b.id AND abp.author_id = ?
            LEFT JOIN book_series bs ON bs.book_id = b.id
            WHERE bs.book_id IS NULL
              AND abp.is_selected = 1
              AND (
                  abp.author_id IS NOT NULL
                  OR EXISTS (
                      SELECT 1 FROM book_authors ba
                      WHERE ba.book_id = b.id AND ba.author_id = ?
                  )
              )
            """,
            (author_id, author_id),
        ).fetchall()
    ]
    return series, books


def _series_authors(connection: Any, series_id: int, *, tracked_only: bool = True) -> list[Any]:
    tracked_clause = "AND a.is_tracked = 1" if tracked_only else ""
    rows = connection.execute(
        f"""
        SELECT DISTINCT a.*
        FROM authors a
        WHERE (
            EXISTS (
                SELECT 1 FROM book_authors ba
                JOIN book_series bs ON bs.book_id = ba.book_id
                WHERE ba.author_id = a.id AND bs.series_id = ?
            )
            OR EXISTS (
                SELECT 1 FROM author_series_preferences asp
                WHERE asp.author_id = a.id AND asp.series_id = ?
            )
        ) {tracked_clause}
        ORDER BY a.name
        """,
        (series_id, series_id),
    ).fetchall()
    rows = [row for row in rows if is_followable_author(row)]
    if not tracked_only:
        return rows
    return [
        row
        for row in rows
        if series_id in _tracked_author_scopes(connection, row["id"])[0]
    ]


def _book_authors(connection: Any, book_id: int, *, tracked_only: bool = True) -> list[Any]:
    tracked_clause = "AND a.is_tracked = 1" if tracked_only else ""
    rows = connection.execute(
        f"""
        SELECT DISTINCT a.*
        FROM authors a
        WHERE (
            EXISTS (
                SELECT 1 FROM book_authors ba
                WHERE ba.author_id = a.id AND ba.book_id = ?
            )
            OR EXISTS (
                SELECT 1 FROM author_book_preferences abp
                WHERE abp.author_id = a.id AND abp.book_id = ?
            )
        ) {tracked_clause}
        ORDER BY a.name
        """,
        (book_id, book_id),
    ).fetchall()
    rows = [row for row in rows if is_followable_author(row)]
    if not tracked_only:
        return rows
    return [
        row
        for row in rows
        if book_id in _tracked_author_scopes(connection, row["id"])[1]
    ]


def _author_with_aliases(connection: Any, row: Any) -> dict[str, Any]:
    author = _decode(row)
    aliases = [
        item["alias_name"]
        for item in connection.execute(
            "SELECT alias_name FROM author_aliases WHERE author_id = ? ORDER BY alias_name",
            (author["id"],),
        ).fetchall()
    ]
    if aliases:
        author["aliases"] = aliases
        display_names = author_display_names([author["name"], *aliases])
        if len(display_names) > 1:
            author["display_name"] = " / ".join(display_names)
    return author


def _author_is_orphaned_by(
    connection: Any,
    author_id: int,
    kind: str,
    entity_id: int,
) -> bool:
    series_ids, book_ids = _tracked_author_scopes(connection, author_id)
    if kind == "series":
        series_ids = [value for value in series_ids if value != entity_id]
    else:
        book_ids = [value for value in book_ids if value != entity_id]
    return not series_ids and not book_ids
