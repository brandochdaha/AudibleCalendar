"""Resolve contributors using catalogue evidence already fetched for a target."""

from .author_identity import _identity_groups, consolidate_authors
from .author_decisions import read_decisions
from .author_sources import record_author_book, record_author_source
from .repository import list_authors
from .repository.named import _upsert_named


def resolve_catalogue_author(db, author, products, marketplace):
    """Retain directly evidenced source IDs without issuing extra API requests."""
    if not products:
        return author
    records = {}
    books = {}
    with db.transaction() as connection:
        decisions = read_decisions(connection)
        for row in connection.execute(
            "SELECT asin, name, author_id, marketplace FROM author_sources WHERE marketplace = ?",
            (marketplace,),
        ):
            records[row["asin"]] = dict(row)
        for row in connection.execute("""
            SELECT s.asin, b.asin AS book_asin FROM author_source_books sb
            JOIN author_sources s ON s.id = sb.source_id JOIN books b ON b.id = sb.book_id
            WHERE s.marketplace = ?
        """, (marketplace,)):
            books.setdefault(row["asin"], set()).add(row["book_asin"])
    records.setdefault(author["asin"], {**author, "marketplace": marketplace})
    for product in products:
        for contributor in product.get("authors") or []:
            asin = contributor.get("asin")
            if asin:
                records.setdefault(asin, {**contributor, "marketplace": marketplace})
                books.setdefault(asin, set()).add(product["asin"])
    members = next(group for group in _identity_groups(list(records.values()), books, decisions)
                   if any(item["asin"] == author["asin"] for item in group))
    # Keep source credits on already cached books even when a match needs
    # review. Sync may otherwise skip old books and discard that evidence.
    target_id = next((item["author_id"] for item in members if item.get("author_id")), None)
    if target_id is None:
        return {**author, "source_asins": [item["asin"] for item in members]}
    with db.transaction() as connection:
        known_books = {row["asin"]: row["id"] for row in connection.execute(
            "SELECT id, asin FROM books WHERE marketplace = ?", (marketplace,))}
        for member in members:
            if not member.get("author_id"):
                record_author_source(connection, target_id, marketplace, member)
                connection.execute("""INSERT OR IGNORE INTO author_aliases (author_id, alias_name)
                    SELECT id, ? FROM authors WHERE id = ? AND name COLLATE NOCASE != ?""",
                    (member["name"], target_id, member["name"]))
            for book_asin in books.get(member["asin"], set()):
                if book_asin in known_books:
                    record_author_book(connection, marketplace, member["asin"], known_books[book_asin])
        observed = {person['asin'] for product in products for person in product.get('authors') or []
                    if person.get('asin')}
        matched = {member['asin'] for member in members}
        for asin in observed - matched:
            shared = books.get(asin, set()) & known_books.keys()
            if not shared:
                continue
            source = records[asin]
            owner = source.get('author_id') or _upsert_named(connection, 'authors', source, marketplace)
            for book_asin in shared:
                book_id = known_books[book_asin]
                record_author_book(connection, marketplace, asin, book_id)
                connection.execute('INSERT OR IGNORE INTO book_authors (book_id, author_id) VALUES (?, ?)',
                                   (book_id, owner))
    db.author_identities_dirty = True
    consolidate_authors(db)
    canonical = next((item for item in list_authors(db, consolidate=False)
                 if item["marketplace"] == marketplace
                 and author["asin"] in [item["asin"], *(item.get("source_asins") or [])]), author)
    return canonical
