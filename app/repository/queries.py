"""Repository queries: local SQLite operations."""

from __future__ import annotations

from datetime import date
from .presentation import columns
from typing import Any
from ..author_search import group_search_author_records
from ..author_sources import attach_sources, sources_by_author
from ..author_decisions import read_decisions, separation_map
from ..author_identity import author_display_names, consolidate_authors
from ..db import Database
from .common import _author_series_scope_sql, _decode, _followed_book_scope_sql, _now, _series_favourite_sql
from .relations import _book_with_relations, _books_with_relations


def _list(db: Database, table: str, tracked_only: bool = False) -> list[dict[str, Any]]:
    with db.transaction() as connection:
        rows = connection.execute(
            f"SELECT {columns(connection, table)} FROM {table} WHERE (? = 0 OR is_tracked = 1) ORDER BY name COLLATE NOCASE ASC, name ASC",
            (int(tracked_only),),
        ).fetchall()
        return [_decode(row) for row in rows]


def list_authors(
    db: Database,
    tracked_only: bool = False,
    *,
    consolidate: bool = True,
) -> list[dict[str, Any]]:
    if consolidate:
        consolidate_authors(db)
    authors = _list(db, "authors", tracked_only)
    if not authors:
        return authors
    author_ids = [author["id"] for author in authors]
    placeholders = ",".join("?" for _ in author_ids)
    with db.transaction() as connection:
        source_records = sources_by_author(connection, author_ids)
        separations = separation_map(read_decisions(connection))
        aliases: dict[int, list[str]] = {}
        for row in connection.execute(
            f"SELECT author_id, alias_name FROM author_aliases WHERE author_id IN ({placeholders}) ORDER BY alias_name",
            tuple(author_ids),
        ):
            aliases.setdefault(row["author_id"], []).append(row["alias_name"])
    for author in authors:
        attach_sources(author, source_records.get(author["id"], []), separations)
        author_aliases = aliases.get(author["id"], [])
        if author_aliases:
            author["aliases"] = author_aliases
            display_names = author_display_names([author["name"], *author_aliases])
            if len(display_names) > 1:
                author["display_name"] = " / ".join(display_names)
    return authors


def list_series(db: Database, tracked_only: bool = False) -> list[dict[str, Any]]:
    with db.transaction() as connection:
        rows = connection.execute(
            f"""
            SELECT {columns(connection, "series", "s")}, {_series_favourite_sql("s")} AS effective_is_favourite
            FROM series s
            WHERE (? = 0 OR s.is_tracked = 1)
            ORDER BY s.name ASC
            """,
            (int(tracked_only),),
        ).fetchall()
        result = []
        for row in rows:
            item = _decode(row)
            item["is_favourite"] = bool(
                item.pop("effective_is_favourite", item.get("is_favourite"))
            )
            result.append(item)
        return result


def list_selected_author_series(db: Database) -> list[dict[str, Any]]:
    """Return distinct series explicitly selected under an author follow.

    These series are intentionally not marked as directly tracked: the
    preference belongs to the author and only narrows that author's releases.
    They are nevertheless independent sync targets so newly published volumes
    are discovered even when the author catalogue search is incomplete.
    """

    with db.transaction() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT s.*
            FROM series s
            JOIN author_series_preferences asp ON asp.series_id = s.id
            JOIN authors a ON a.id = asp.author_id
            WHERE asp.is_selected = 1 AND a.is_tracked = 1
            ORDER BY s.name ASC
            """
        ).fetchall()
        return [_decode(row) for row in rows]


def list_followed_series(
    db: Database,
    *,
    include_sync_metadata: bool = False,
    consolidate: bool = True,
) -> list[dict[str, Any]]:
    """Return directly followed series plus series inherited from followed authors."""

    # Keep the series tab aligned with the canonical author identities before
    # resolving the provenance names below.  The watches endpoint lists
    # authors first, but this function is also used directly by repository
    # callers and tests.
    if consolidate:
        consolidate_authors(db)
    with db.transaction() as connection:
        rows = connection.execute(
            f"""
            SELECT {columns(connection, "series", "s")},
                   (EXISTS (
                       SELECT 1 FROM book_series bs
                       JOIN book_authors ba ON ba.book_id = bs.book_id
                       JOIN authors a ON a.id = ba.author_id
                       WHERE bs.series_id = s.id AND a.is_tracked = 1
                         AND {_author_series_scope_sql("a", "s.id")}
                   ) OR EXISTS (
                       SELECT 1 FROM author_series_preferences asp
                       JOIN authors a ON a.id = asp.author_id
                       WHERE asp.series_id = s.id AND asp.is_selected = 1 AND a.is_tracked = 1
                   )) AS followed_via_author,
                   EXISTS (
                       SELECT 1 FROM author_series_preferences asp
                       JOIN authors a ON a.id = asp.author_id
                       WHERE asp.series_id = s.id AND asp.is_selected = 1
                         AND a.is_tracked = 1
                   ) AS selected_via_author,
                   {_series_favourite_sql("s")} AS effective_is_favourite
            FROM series s
            WHERE s.is_tracked = 1
               OR EXISTS (
                   SELECT 1 FROM book_series bs
                   JOIN book_authors ba ON ba.book_id = bs.book_id
                   JOIN authors a ON a.id = ba.author_id
                   WHERE bs.series_id = s.id AND a.is_tracked = 1
                     AND {_author_series_scope_sql("a", "s.id")}
               )
               OR EXISTS (
                   SELECT 1 FROM author_series_preferences asp
                   JOIN authors a ON a.id = asp.author_id
                   WHERE asp.series_id = s.id AND asp.is_selected = 1
                     AND a.is_tracked = 1
               )
            ORDER BY s.name ASC
            """
        ).fetchall()
        result = []
        series_ids = [row["id"] for row in rows]
        followed_authors_by_series: dict[int, list[dict[str, Any]]] = {series_id: [] for series_id in series_ids}
        if series_ids:
            placeholders = ", ".join("?" for _ in series_ids)
            author_rows = connection.execute(
                f"""
                WITH series_authors AS (
                    SELECT DISTINCT bs.series_id, ba.author_id
                    FROM book_series bs
                    JOIN book_authors ba ON ba.book_id = bs.book_id
                    WHERE bs.series_id IN ({placeholders})
                    UNION
                    SELECT DISTINCT asp.series_id, asp.author_id
                    FROM author_series_preferences asp
                    WHERE asp.series_id IN ({placeholders}) AND asp.is_selected = 1
                )
                SELECT sa.series_id, a.id AS author_id, a.asin, a.marketplace, a.name,
                       a.follow_all, a.is_favourite,
                       EXISTS (
                           SELECT 1
                           FROM author_series_preferences selected
                           WHERE selected.author_id = a.id
                             AND selected.series_id = sa.series_id
                             AND selected.is_selected = 1
                       ) AS selected_via_author
                FROM series_authors sa
                JOIN authors a ON a.id = sa.author_id
                WHERE a.is_tracked = 1
                  AND {_author_series_scope_sql("a", "sa.series_id")}
                ORDER BY sa.series_id, a.name
                """,
                (*series_ids, *series_ids),
            ).fetchall()
            author_ids = {row["author_id"] for row in author_rows}
            source_records = sources_by_author(connection, author_ids)
            separations = separation_map(read_decisions(connection))
            aliases_by_author: dict[int, list[str]] = {author_id: [] for author_id in author_ids}
            if author_ids:
                author_placeholders = ", ".join("?" for _ in author_ids)
                alias_rows = connection.execute(
                    f"""
                    SELECT author_id, alias_name
                    FROM author_aliases
                    WHERE author_id IN ({author_placeholders})
                    ORDER BY alias_name
                    """,
                    tuple(author_ids),
                ).fetchall()
                for alias_row in alias_rows:
                    aliases_by_author.setdefault(alias_row["author_id"], []).append(alias_row["alias_name"])
            for author_row in author_rows:
                aliases = aliases_by_author.get(author_row["author_id"], [])
                names = [author_row["name"], *aliases]
                display_names = author_display_names(names)
                display_name = " / ".join(display_names)
                author_record = {
                    "id": author_row["author_id"],
                    "asin": author_row["asin"],
                    "marketplace": author_row["marketplace"],
                    "name": author_row["name"],
                    "display_name": display_name,
                    "source": "Selected via author" if author_row["selected_via_author"] else "Via author",
                }
                attach_sources(author_record, source_records.get(author_row["author_id"], []), separations)
                if include_sync_metadata:
                    author_record.update({
                        "follow_all": bool(author_row["follow_all"]),
                        "is_favourite": bool(author_row["is_favourite"]),
                    })
                followed_authors_by_series[author_row["series_id"]].append(author_record)
            for series_id, authors in followed_authors_by_series.items():
                followed_authors_by_series[series_id] = group_search_author_records(authors)
        for row in rows:
            item = _decode(row)
            item["is_directly_tracked"] = bool(item.get("is_tracked"))
            item["followed_via_author"] = bool(item.get("followed_via_author"))
            item["selected_via_author"] = bool(item.get("selected_via_author"))
            item["is_favourite"] = bool(
                item.pop("effective_is_favourite", item.get("is_favourite"))
            )
            item["followed_authors"] = followed_authors_by_series.get(item["id"], [])
            result.append(item)
        return result


def list_books(
    db: Database,
    tracked_only: bool = False,
    *,
    consolidate: bool = True,
) -> list[dict[str, Any]]:
    if consolidate:
        consolidate_authors(db)
    with db.transaction() as connection:
        rows = connection.execute(
            f"SELECT {columns(connection, 'books')} FROM books WHERE (? = 0 OR is_tracked = 1) ORDER BY release_date IS NULL, release_date, title",
            (int(tracked_only),),
        ).fetchall()
        return _books_with_relations(connection, rows)


def get_book(db: Database, asin: str, marketplace: str) -> dict[str, Any] | None:
    """Load one local product without expanding the rest of the catalogue."""
    with db.transaction() as connection:
        row = connection.execute(
            f"SELECT {columns(connection, 'books')} FROM books WHERE asin = ? AND marketplace = ?", (asin, marketplace)
        ).fetchone()
        return _book_with_relations(connection, row) if row else None


def list_followed_books(db: Database, *, consolidate: bool = True) -> list[dict[str, Any]]:
    """Return books followed directly, through an author, or through a series."""

    if consolidate:
        consolidate_authors(db)
    with db.transaction() as connection:
        rows = connection.execute(
            f"""
            SELECT {columns(connection, "books", "b")} FROM books b
            WHERE {_followed_book_scope_sql("b")}
            ORDER BY b.release_date IS NULL, b.release_date, b.title
            """
        ).fetchall()
        return _books_with_relations(connection, rows)


def calendar(
    db: Database,
    start: str,
    end: str,
    english_only: bool = False,
    include_past: bool = False,
    *,
    limit: int | None = None,
    descending: bool = False,
    favourites_only: bool = False,
    today: date | None = None,
) -> list[dict[str, Any]]:
    # The normal calendar is an upcoming-release view; the optional history
    # mode is deliberately limited to rows already cached in the local DB.
    effective_start = start[:10] if include_past else max(start[:10], (today or date.today()).isoformat())
    consolidate_authors(db)
    with db.transaction() as connection:
        rows = connection.execute(
            f"""
            SELECT {columns(connection, "books", "b")} FROM books b
            WHERE b.release_date IS NOT NULL AND b.release_date >= ? AND b.release_date <= ?
              AND COALESCE(b.is_available, 1) != 0
              AND (
                ? = 0 OR b.language IS NULL OR TRIM(b.language) = ''
                OR LOWER(b.language) IN ('en', 'eng', 'english')
                OR REPLACE(LOWER(b.language), '_', '-') LIKE 'en-%'
                OR LOWER(b.language) LIKE 'english %'
              )
              AND {_followed_book_scope_sql("b")}
            ORDER BY b.release_date {"DESC" if descending else "ASC"}, b.title
            LIMIT ?
            """,
            (effective_start, end, int(english_only), limit if limit is not None and not favourites_only else -1),
        )
        if favourites_only:
            result = []
            while batch := rows.fetchmany(100):
                result.extend(book for book in _books_with_relations(connection, batch) if book.get("is_favourite"))
                if limit is not None and len(result) >= limit:
                    return result[:limit]
            return result
        return _books_with_relations(connection, rows.fetchall())


def search_local(db: Database, query: str, kind: str) -> list[dict[str, Any]]:
    pattern = f"%{query}%"
    table = {"author": "authors", "series": "series", "book": "books"}.get(kind)
    if not table:
        raise ValueError("Unknown search kind")
    field = "title" if kind == "book" else "name"
    with db.transaction() as connection:
        rows = connection.execute(
            f"SELECT {columns(connection, table)} FROM {table} WHERE {field} LIKE ? COLLATE NOCASE ORDER BY {field} LIMIT 20",
            (pattern,),
        ).fetchall()
        return [_decode(row) for row in rows]


def sync_state(db: Database) -> list[dict[str, Any]]:
    with db.transaction() as connection:
        return [_decode(row) for row in connection.execute("SELECT * FROM sync_state ORDER BY target_type, target_id").fetchall()]


def update_sync_state(db: Database, target_type: str, target_id: int, *, started: bool = False, success: bool = False, error: str | None = None, items_seen: int | None = None) -> None:
    with db.transaction() as connection:
        current = connection.execute("SELECT * FROM sync_state WHERE target_type = ? AND target_id = ?", (target_type, target_id)).fetchone()
        started_at = _now() if started else (current["last_started_at"] if current else None)
        success_at = _now() if success else (current["last_success_at"] if current else None)
        count = items_seen if items_seen is not None else (current["items_seen"] if current else 0)
        connection.execute(
            """
            INSERT INTO sync_state (target_type, target_id, last_started_at, last_success_at, last_error, items_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_type, target_id) DO UPDATE SET
                last_started_at=excluded.last_started_at, last_success_at=excluded.last_success_at,
                last_error=excluded.last_error, items_seen=excluded.items_seen
            """,
            (target_type, target_id, started_at, success_at, error, count),
        )
