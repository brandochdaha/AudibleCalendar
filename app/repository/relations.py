"""Repository relations: local SQLite operations."""

from __future__ import annotations

from typing import Any
from .presentation import columns
from ..author_search import group_search_author_records
from ..author_identity import collapse_author_records
from ..author_sources import attach_sources, source_asins, sources_by_author
from ..author_decisions import read_decisions, separation_map
from ..normalization import is_followable_author
from .common import _author_book_scope_sql, _author_series_scope_sql, _decode, _series_favourite_sql


def _book_with_relations(connection: Any, row: Any) -> dict[str, Any]:
    return _books_with_relations(connection, [row])[0]


def _books_with_relations(connection: Any, rows: list[Any]) -> list[dict[str, Any]]:
    """Expand a set of books with a fixed number of bulk relation queries."""

    if len(rows) > 500:
        return [book for offset in range(0, len(rows), 500)
                for book in _books_with_relations(connection, rows[offset:offset + 500])]
    books = [_decode(row) for row in rows]
    if not books:
        return books
    parameters = tuple(book["id"] for book in books)
    placeholders = ",".join("?" for _ in parameters)
    books_by_id = {book["id"]: book for book in books}
    authors_by_book: dict[int, list[dict[str, Any]]] = {book_id: [] for book_id in books_by_id}
    separations = separation_map(read_decisions(connection))
    author_rows = connection.execute(
        f"""
        SELECT ba.book_id AS relation_book_id, {columns(connection, "authors", "a")}, ba.role
        FROM book_authors ba
        JOIN authors a ON a.id = ba.author_id
        WHERE ba.book_id IN ({placeholders})
        ORDER BY ba.book_id, a.name
        """, parameters
    ).fetchall()
    sources = sources_by_author(connection, (row["id"] for row in author_rows))
    for row in author_rows:
        book_id = row["relation_book_id"]
        if book_id not in books_by_id:
            continue
        author = _decode(row)
        author.pop("relation_book_id", None)
        attach_sources(author, sources.get(author["id"], []), separations)
        if is_followable_author(author):
            authors_by_book[book_id].append(author)

    series_by_book: dict[int, list[dict[str, Any]]] = {book_id: [] for book_id in books_by_id}
    favourite_series_book_ids: set[int] = set()
    for row in connection.execute(
        f"""
        SELECT bs.book_id AS relation_book_id, {columns(connection, "series", "s")},
               {_series_favourite_sql("s")} AS effective_is_favourite,
               bs.sequence_text, bs.sequence_number
        FROM book_series bs
        JOIN series s ON s.id = bs.series_id
        WHERE bs.book_id IN ({placeholders})
        ORDER BY bs.book_id, bs.sequence_number, s.name
        """, parameters
    ).fetchall():
        book_id = row["relation_book_id"]
        if book_id in books_by_id:
            series = _decode(row)
            series.pop("relation_book_id", None)
            series["is_favourite"] = bool(
                series.pop("effective_is_favourite", series.get("is_favourite"))
            )
            series_by_book[book_id].append(series)
            if series.get("is_favourite"):
                favourite_series_book_ids.add(book_id)

    author_scope_rows = connection.execute(
        f"""
            SELECT ba.book_id,
                   MAX(CASE WHEN a.is_favourite = 1 AND (
                       (
                           NOT EXISTS (SELECT 1 FROM book_series WHERE book_id = ba.book_id)
                           AND (
                               (a.follow_all = 1 AND NOT EXISTS (
                                   SELECT 1 FROM author_book_preferences excluded_abp
                                   WHERE excluded_abp.author_id = a.id
                                     AND excluded_abp.book_id = ba.book_id
                                     AND excluded_abp.is_selected = 0
                               ))
                               OR EXISTS (
                                   SELECT 1 FROM author_book_preferences selected_abp
                                   WHERE selected_abp.author_id = a.id
                                     AND selected_abp.book_id = ba.book_id
                                     AND selected_abp.is_selected = 1
                               )
                           )
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM book_series favourite_bs
                           JOIN series favourite_series ON favourite_series.id = favourite_bs.series_id
                           WHERE favourite_bs.book_id = ba.book_id
                             AND (
                                 favourite_series.favourite_override = 1
                                 OR (
                                     favourite_series.favourite_override IS NULL
                                     AND {_author_series_scope_sql("a", "favourite_series.id")}
                                 )
                             )
                       )
                   ) THEN 1 ELSE 0 END) AS has_favourite_author
            FROM book_authors ba
            JOIN authors a ON a.id = ba.author_id
            WHERE a.is_tracked = 1 AND ba.book_id IN ({placeholders})
              AND {_author_book_scope_sql("a", "ba.book_id")}
            GROUP BY ba.book_id
        """, parameters
    ).fetchall()
    author_tracked_book_ids = {row["book_id"] for row in author_scope_rows}
    favourite_author_book_ids = {
        row["book_id"] for row in author_scope_rows if row["has_favourite_author"]
    }
    for book in books:
        book_id = book["id"]
        authors = collapse_author_records(authors_by_book[book_id])
        books_by_author = {
            asin: {str(book.get("asin"))}
            for author in authors
            for asin in source_asins(author)
        }
        book["authors"] = group_search_author_records(authors, books_by_author)
        book["series"] = series_by_book[book_id]
        reasons: list[str] = []
        if book.get("is_tracked"):
            reasons.append("Direct")
        if book_id in author_tracked_book_ids:
            reasons.append("Author")
        if any(item.get("is_tracked") for item in book["series"]):
            reasons.append("Series")
        book["tracking_reasons"] = reasons
        if book.get("favourite_override") is not None:
            book["is_favourite"] = bool(book["favourite_override"])
        else:
            book["is_favourite"] = (
                book_id in favourite_author_book_ids
                or book_id in favourite_series_book_ids
            )
    return books
