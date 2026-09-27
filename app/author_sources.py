"""Original catalogue identities retained beneath canonical author rows."""

import json

from .normalization import person_name_fingerprint


SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS author_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    marketplace TEXT NOT NULL,
    asin TEXT NOT NULL,
    name TEXT NOT NULL,
    source_payload TEXT,
    UNIQUE (marketplace, asin)
);
CREATE INDEX IF NOT EXISTS idx_author_sources_author ON author_sources(author_id);
CREATE TABLE IF NOT EXISTS author_source_books (
    source_id INTEGER NOT NULL REFERENCES author_sources(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, book_id)
);
"""

SOURCE_BACKFILL = """
INSERT OR IGNORE INTO author_sources (author_id, marketplace, asin, name, source_payload)
SELECT id, marketplace, asin, name, source_payload FROM authors;
INSERT OR IGNORE INTO author_source_books (source_id, book_id)
SELECT s.id, ba.book_id FROM author_sources s
JOIN book_authors ba ON ba.author_id = s.author_id;
"""


def record_author_source(connection, author_id, marketplace, item):
    connection.execute(
        """INSERT INTO author_sources (author_id, marketplace, asin, name, source_payload)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(marketplace, asin) DO UPDATE SET
            name=excluded.name, source_payload=excluded.source_payload""",
        (author_id, marketplace, str(item["asin"]), item.get("name") or "Unnamed",
         json.dumps(item.get("payload") or item, ensure_ascii=False)),
    )


def record_author_book(connection, marketplace, asin, book_id):
    connection.execute(
        """INSERT OR IGNORE INTO author_source_books (source_id, book_id)
        SELECT id, ? FROM author_sources WHERE marketplace = ? AND asin = ?""",
        (book_id, marketplace, asin),
    )


def source_asins(author):
    return {str(value) for value in [author.get("asin"), *(author.get("source_asins") or [])] if value}


def author_search_names(author, fallback=None):
    """Return distinct catalogue names retained beneath one canonical author."""
    values = [author.get("name") or fallback]
    values.extend(source.get("name") for source in author.get("source_identities") or [])
    values.extend(author.get("aliases") or [])
    result = []
    fingerprints = set()
    for raw in values:
        name = str(raw or "").strip()
        fingerprint = person_name_fingerprint(name)
        if name and fingerprint and fingerprint not in fingerprints:
            result.append(name)
            fingerprints.add(fingerprint)
    fallback = str(fallback or "").strip()
    return result or ([fallback] if fallback else [])


def sources_by_author(connection, author_ids=None):
    ids = sorted({int(author_id) for author_id in author_ids or []})
    if author_ids is not None and not ids:
        return {}
    where = f" WHERE author_id IN ({','.join('?' for _ in ids)})" if ids else ""
    result = {}
    for row in connection.execute(
        f"SELECT author_id, asin, name FROM author_sources{where} ORDER BY id", tuple(ids)
    ):
        result.setdefault(row["author_id"], []).append({"asin": row["asin"], "name": row["name"]})
    return result


def identities_separate(left, right):
    if left.get("marketplace") and right.get("marketplace") and left["marketplace"] != right["marketplace"]:
        return True
    return bool(
        source_asins(left) & set(right.get("separate_source_asins") or [])
        or source_asins(right) & set(left.get("separate_source_asins") or [])
    )


def attach_sources(author, sources, separations=None):
    if author.get("id") is not None:
        author["canonical_id"] = author["id"]
    if len(sources) > 1:
        author["source_asins"] = [source["asin"] for source in sources]
        author["source_identities"] = sources
    if separations:
        blocked = set()
        for asin in source_asins(author):
            blocked.update(separations.get((author.get("marketplace"), asin), set()))
        if blocked:
            author["separate_source_asins"] = sorted(blocked)
    return author
