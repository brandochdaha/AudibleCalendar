"""Build watchlist chips in batches, regardless of the number of authors."""

from .presentation import columns
from .common import _decode, _series_favourite_sql
from ..author_search import group_search_author_records
from ..author_sources import source_asins
from .queries import list_authors, list_followed_books, list_followed_series
from .relations import _books_with_relations


def watchlist(db):
    authors = list_authors(db, tracked_only=True)
    authors_by_id = {author["id"]: author for author in authors}
    for author in authors:
        author.update(selected_series=[], selected_books=[], series_filter_active=not author["follow_all"])
    with db.transaction() as connection:
        books_by_author = {}
        for row in connection.execute("""
            SELECT ba.author_id, b.asin
            FROM book_authors ba JOIN books b ON b.id = ba.book_id
        """):
            author = authors_by_id.get(row["author_id"])
            if not author:
                continue
            for asin in source_asins(author):
                books_by_author.setdefault(asin, set()).add(str(row["asin"]))
        overrides = connection.execute("""
            SELECT author_id FROM author_series_preferences
            UNION SELECT author_id FROM author_book_preferences
        """).fetchall()
        for row in overrides:
            if row["author_id"] in authors_by_id:
                authors_by_id[row["author_id"]]["series_filter_active"] = True
        series_rows = connection.execute(f"""
            WITH known AS (
                SELECT ba.author_id, bs.series_id
                FROM book_authors ba JOIN book_series bs ON bs.book_id = ba.book_id
                UNION SELECT author_id, series_id FROM author_series_preferences
            )
            SELECT {columns(connection, "series", "s")}, known.author_id, {_series_favourite_sql("s")} AS effective_is_favourite
            FROM known JOIN authors a ON a.id = known.author_id
            JOIN series s ON s.id = known.series_id
            LEFT JOIN author_series_preferences p ON p.author_id = a.id AND p.series_id = s.id
            WHERE a.is_tracked = 1 AND (
                (a.follow_all = 1 AND COALESCE(p.is_selected, 1) = 1)
                OR (a.follow_all = 0 AND p.is_selected = 1)
            ) ORDER BY s.name
        """).fetchall()
        for row in series_rows:
            item = _decode(row)
            author_id = item.pop("author_id")
            item["is_favourite"] = bool(item.pop("effective_is_favourite"))
            if author_id in authors_by_id:
                authors_by_id[author_id]["selected_series"].append(item)
        book_rows = connection.execute(f"""
            WITH known AS (
                SELECT author_id, book_id FROM book_authors
                UNION SELECT author_id, book_id FROM author_book_preferences
            )
            SELECT {columns(connection, "books", "b")}, known.author_id
            FROM known JOIN authors a ON a.id = known.author_id
            JOIN books b ON b.id = known.book_id
            LEFT JOIN author_book_preferences p ON p.author_id = a.id AND p.book_id = b.id
            WHERE a.is_tracked = 1
              AND NOT EXISTS (SELECT 1 FROM book_series WHERE book_id = b.id)
              AND (
                (a.follow_all = 1 AND COALESCE(p.is_selected, 1) = 1)
                OR (a.follow_all = 0 AND p.is_selected = 1)
            ) ORDER BY b.title
        """).fetchall()
        unique_books = {row["id"]: {key: row[key] for key in row.keys() if key != "author_id"} for row in book_rows}
        books_by_id = {book["id"]: book for book in _books_with_relations(connection, list(unique_books.values()))}
        for row in book_rows:
            if row["author_id"] in authors_by_id:
                authors_by_id[row["author_id"]]["selected_books"].append(books_by_id[row["id"]])
    books = list_followed_books(db, consolidate=False)
    for book in books:
        book["is_individually_followed"] = bool(book.get("is_tracked") and not book.get("series"))
    return {
        "authors": group_search_author_records(authors, books_by_author),
        "series": list_followed_series(db, consolidate=False),
        "books": books,
    }
