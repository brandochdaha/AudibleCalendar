"""Repository unfollow: local SQLite operations."""

from __future__ import annotations

from typing import Any
from ..db import Database
from .common import _decode, _now
from .favourites import _clear_orphaned_favourites
from .preferences import _upsert_book_selection, _upsert_series_selection
from .scopes import _author_is_orphaned_by, _author_with_aliases, _book_authors, _series_authors, _tracked_author_scopes


def _unfollow_series_connection(
    connection: Any,
    series_id: int,
    *,
    skip_author_ids: set[int] | None = None,
) -> None:
    # This removes the direct series relationship only. Author-scoped
    # selections are independent and must survive a shared-series removal.
    now = _now()
    connection.execute(
        "UPDATE series SET is_tracked = 0, updated_at = ? WHERE id = ?",
        (now, series_id),
    )
    # A directly followed book remains independent if it has its own follow
    # intent. Only the effective-favourite cleanup is source-aware here.
    _clear_orphaned_favourites(connection, series_ids={series_id})


def _unfollow_book_connection(
    connection: Any,
    book_id: int,
    *,
    skip_author_ids: set[int] | None = None,
) -> None:
    if connection.execute("SELECT 1 FROM book_series WHERE book_id = ? LIMIT 1", (book_id,)).fetchone():
        raise ValueError("Books in a series must be unfollowed through their series")
    now = _now()
    connection.execute(
        "UPDATE books SET is_tracked = 0, updated_at = ? WHERE id = ?",
        (now, book_id),
    )
    _clear_orphaned_favourites(connection, book_ids={book_id})


def unfollow_preview(db: Database, kind: str, entity_id: int) -> dict[str, Any]:
    """Describe a removal and the author relationships attached to its item."""

    if kind not in {"author", "series", "book"}:
        raise ValueError("Unknown watch kind")
    with db.transaction() as connection:
        if kind == "author":
            author = connection.execute("SELECT * FROM authors WHERE id = ?", (entity_id,)).fetchone()
            if not author:
                raise ValueError("Author not found")
            series_ids, book_ids = _tracked_author_scopes(connection, entity_id)
            series = []
            books = []
            if series_ids:
                placeholders = ", ".join("?" for _ in series_ids)
                series = [_decode(row) for row in connection.execute(
                    f"SELECT * FROM series WHERE id IN ({placeholders}) ORDER BY name",
                    tuple(series_ids),
                ).fetchall()]
            if book_ids:
                placeholders = ", ".join("?" for _ in book_ids)
                books = [_decode(row) for row in connection.execute(
                    f"SELECT * FROM books WHERE id IN ({placeholders}) ORDER BY title",
                    tuple(book_ids),
                ).fetchall()]
            shared: list[dict[str, Any]] = []
            for child_kind, children in (("series", series), ("book", books)):
                for child in children:
                    authors = (
                        _series_authors(connection, child["id"])
                        if child_kind == "series"
                        else _book_authors(connection, child["id"])
                    )
                    others = [
                        _author_with_aliases(connection, row)
                        for row in authors
                        if row["id"] != entity_id
                    ]
                    if others:
                        shared.append({
                            "kind": child_kind,
                            "id": child["id"],
                            "name": child.get("name") or child.get("title"),
                            "authors": others,
                        })
            return {
                "kind": kind,
                "target": _author_with_aliases(connection, author),
                "series": series,
                "books": books,
                "shared_impacts": shared,
                "eligible_authors": [],
            }

        table = "series" if kind == "series" else "books"
        target = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
        if not target:
            raise ValueError(f"{kind.title()} not found")
        if kind == "book" and connection.execute(
            "SELECT 1 FROM book_series WHERE book_id = ? LIMIT 1", (entity_id,)
        ).fetchone():
            raise ValueError("Books in a series must be unfollowed through their series")
        authors = _series_authors(connection, entity_id) if kind == "series" else _book_authors(connection, entity_id)
        eligible = []
        affected = []
        for row in authors:
            record = _author_with_aliases(connection, row)
            record["would_be_empty"] = _author_is_orphaned_by(connection, row["id"], kind, entity_id)
            affected.append(record)
            if record["would_be_empty"]:
                eligible.append(record)
        result = {
            "kind": kind,
            "target": _decode(target),
            "affected_authors": affected,
            "eligible_authors": eligible,
        }
        if kind == "series":
            result["book_count"] = connection.execute(
                "SELECT COUNT(DISTINCT book_id) AS count FROM book_series WHERE series_id = ?",
                (entity_id,),
            ).fetchone()["count"]
        return result


def unfollow_item(
    db: Database,
    kind: str,
    entity_id: int,
    *,
    author_ids: list[int] | None = None,
    remove_direct: bool | None = None,
) -> None:
    """Remove a direct series/book follow or selected author relationships."""

    if kind not in {"series", "book"}:
        raise ValueError("Only series and standalone books use this removal operation")
    requested = set(author_ids or [])
    if remove_direct is None:
        remove_direct = not requested
    with db.transaction() as connection:
        authors = _series_authors(connection, entity_id, tracked_only=False) if kind == "series" else _book_authors(connection, entity_id, tracked_only=False)
        author_ids_in_item = {row["id"] for row in authors}
        if not requested.issubset(author_ids_in_item):
            raise ValueError("The selected author is not attached to this item")
        if requested:
            # A child can be removed from one author's scope while remaining
            # selected for every other author. Broad author follows retain
            # their all-releases setting and receive an exclusion instead.
            for author_id in requested:
                if kind == "series":
                    _upsert_series_selection(connection, author_id, entity_id, False, preserve_follow_all=True)
                else:
                    _upsert_book_selection(connection, author_id, entity_id, False, preserve_follow_all=True)
            # Following an author is independent of selecting their releases.
            # Keep the account's author follow and exclusions even when empty.
        if not remove_direct:
            if kind == "series":
                _clear_orphaned_favourites(connection, series_ids={entity_id})
            else:
                _clear_orphaned_favourites(connection, book_ids={entity_id})
            return
        if kind == "series":
            _unfollow_series_connection(connection, entity_id)
        else:
            _unfollow_book_connection(connection, entity_id)


def stop_following(db: Database, kind: str, entity_id: int) -> None:
    """Compatibility removal used by older clients and author confirmation."""

    if kind in {"series", "book"}:
        unfollow_item(db, kind, entity_id)
        return
    if kind != "author":
        raise ValueError("Unknown watch kind")
    with db.transaction() as connection:
        author = connection.execute("SELECT id FROM authors WHERE id = ?", (entity_id,)).fetchone()
        if not author:
            return
        # Removing an author must not mutate the shared series/book rows. Any
        # direct child follow, or another author's scope, remains intact.
        connection.execute(
            "UPDATE authors SET is_tracked = 0, follow_all = 0, is_favourite = 0, updated_at = ? WHERE id = ?",
            (_now(), entity_id),
        )
        connection.execute("DELETE FROM author_series_preferences WHERE author_id = ?", (entity_id,))
        connection.execute("DELETE FROM author_book_preferences WHERE author_id = ?", (entity_id,))
        _clear_orphaned_favourites(connection)
