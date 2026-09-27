"""Transactional changes to canonical author rows and their source identities."""

from datetime import datetime, timezone
import json
from typing import Any


def _now():
    return datetime.now(timezone.utc).isoformat()


def _merge_preferences(connection, canonical, duplicate):
    for table, entity in (("author_series_preferences", "series_id"), ("author_book_preferences", "book_id")):
        preferences = {row['id']: {} for row in (canonical, duplicate)}
        for row in connection.execute(f"SELECT * FROM {table} WHERE author_id IN (?, ?)",
                                      (canonical['id'], duplicate['id'])):
            preferences[row['author_id']][row[entity]] = bool(row['is_selected'])
        for entity_id in set(preferences[canonical['id']]) | set(preferences[duplicate['id']]):
            selected = any(
                author['is_tracked'] and preferences[author['id']].get(entity_id, bool(author['follow_all']))
                for author in (canonical, duplicate)
            )
            connection.execute(f"""INSERT INTO {table} (author_id, {entity}, is_selected, updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(author_id, {entity}) DO UPDATE SET
                is_selected=excluded.is_selected, updated_at=excluded.updated_at""",
                (canonical['id'], entity_id, int(selected), _now()))
        connection.execute(f"DELETE FROM {table} WHERE author_id = ?", (duplicate['id'],))


def _merge_author_rows(connection: Any, canonical_id: int, duplicate_id: int) -> None:
    if canonical_id == duplicate_id:
        return
    canonical = connection.execute("SELECT * FROM authors WHERE id = ?", (canonical_id,)).fetchone()
    duplicate = connection.execute("SELECT * FROM authors WHERE id = ?", (duplicate_id,)).fetchone()
    if not canonical or not duplicate:
        return
    _merge_preferences(connection, canonical, duplicate)

    # A cached bibliography may omit books from the other source identity.
    # Invalidate only assembled author details, preserving upstream responses.
    connection.execute("""DELETE FROM source_cache WHERE source = 'audible' AND operation = 'detail'
        AND json_valid(cache_key) AND json_extract(cache_key, '$.parameters.kind') = 'author'
        AND json_extract(cache_key, '$.parameters.region') = ?
        AND json_extract(cache_key, '$.parameters.asin') IN (
            SELECT asin FROM author_sources WHERE author_id IN (?, ?))""",
        (canonical["marketplace"], canonical_id, duplicate_id))

    connection.execute(
        """
        UPDATE authors SET
            description=COALESCE(description, ?),
            image_url=CASE WHEN COALESCE(image_url, '') = '' THEN ? ELSE image_url END,
            audnexus_asin=COALESCE(audnexus_asin, ?),
            is_tracked=MAX(is_tracked, ?),
            follow_all=MAX(follow_all, ?),
            is_favourite=MAX(is_favourite, ?),
            updated_at=?
        WHERE id=?
        """,
        (
            duplicate["description"],
            duplicate["image_url"] or "",
            duplicate["audnexus_asin"],
            duplicate["is_tracked"],
            duplicate["follow_all"],
            duplicate["is_favourite"],
            _now(),
            canonical_id,
        ),
    )

    connection.execute(
        """
        INSERT OR IGNORE INTO book_authors (book_id, author_id, role)
        SELECT book_id, ?, role FROM book_authors WHERE author_id = ?
        """,
        (canonical_id, duplicate_id),
    )
    connection.execute("DELETE FROM book_authors WHERE author_id = ?", (duplicate_id,))
    alias_names = [duplicate["name"]]
    alias_names.extend(
        row["alias_name"]
        for row in connection.execute(
            "SELECT alias_name FROM author_aliases WHERE author_id = ?", (duplicate_id,)
        ).fetchall()
    )
    for alias_name in alias_names:
        if alias_name and alias_name.casefold() != (canonical["name"] or "").casefold():
            connection.execute(
                "INSERT OR IGNORE INTO author_aliases (author_id, alias_name) VALUES (?, ?)",
                (canonical_id, alias_name),
            )
    connection.execute("DELETE FROM author_aliases WHERE author_id = ?", (duplicate_id,))
    connection.execute("UPDATE author_sources SET author_id = ? WHERE author_id = ?", (canonical_id, duplicate_id))
    connection.execute("DELETE FROM sync_state WHERE target_type = 'author' AND target_id = ?", (duplicate_id,))
    connection.execute("DELETE FROM authors WHERE id = ?", (duplicate_id,))


def split_author_sources(connection, author_id, source_asins):
    """Detach a reviewed set of source identities, retaining their book credits."""
    original = connection.execute('SELECT * FROM authors WHERE id = ?', (author_id,)).fetchone()
    sources = connection.execute('SELECT * FROM author_sources WHERE author_id = ?', (author_id,)).fetchall()
    selected = [row for row in sources if row['asin'] in source_asins]
    if not selected or len(selected) == len(sources) or original['asin'] in source_asins:
        raise ValueError('Choose a proper subset that does not contain the primary author identity')
    primary = min(selected, key=lambda row: (row['asin'].startswith('name:'), row['id']))
    try:
        payload = json.loads(primary['source_payload'] or '{}')
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    new_id = connection.execute("""INSERT INTO authors
        (marketplace, asin, name, description, image_url, is_tracked, follow_all, is_favourite, source_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (original['marketplace'], primary['asin'], primary['name'], payload.get('description'),
         payload.get('image_url') or '', original['is_tracked'], original['follow_all'],
         original['is_favourite'], primary['source_payload'])).lastrowid
    connection.executemany('UPDATE author_sources SET author_id = ? WHERE id = ?',
                           ((new_id, row['id']) for row in selected))
    connection.execute("""INSERT INTO book_authors (book_id, author_id, role)
        SELECT DISTINCT sb.book_id, ?, ba.role FROM author_source_books sb
        JOIN author_sources s ON s.id = sb.source_id
        LEFT JOIN book_authors ba ON ba.book_id = sb.book_id AND ba.author_id = ?
        WHERE s.author_id = ?""", (new_id, author_id, new_id))
    connection.execute("""DELETE FROM book_authors WHERE author_id = ?
        AND book_id IN (SELECT book_id FROM book_authors WHERE author_id = ?)
        AND NOT EXISTS (SELECT 1 FROM author_source_books sb
            JOIN author_sources s ON s.id = sb.source_id
            WHERE s.author_id = ? AND sb.book_id = book_authors.book_id)""",
        (author_id, new_id, author_id))
    for table, entity, scope in (
        ('author_book_preferences', 'book_id', 'SELECT book_id FROM book_authors WHERE author_id = ?'),
        ('author_series_preferences', 'series_id', '''SELECT bs.series_id FROM book_series bs
            JOIN book_authors ba ON ba.book_id = bs.book_id WHERE ba.author_id = ?'''),
    ):
        connection.execute(f"""INSERT INTO {table} (author_id, {entity}, is_selected, created_at, updated_at)
            SELECT ?, {entity}, is_selected, created_at, ? FROM {table}
            WHERE author_id = ? AND {entity} IN ({scope})""", (new_id, _now(), author_id, new_id))
        connection.execute(f"""DELETE FROM {table} WHERE author_id = ?
            AND {entity} IN ({scope}) AND {entity} NOT IN ({scope})""", (author_id, new_id, author_id))
    for source in selected:
        connection.execute('DELETE FROM author_aliases WHERE author_id = ? AND alias_name = ?',
                           (author_id, source['name']))
        if source['name'].casefold() != primary['name'].casefold():
            connection.execute('INSERT OR IGNORE INTO author_aliases (author_id, alias_name) VALUES (?, ?)',
                               (new_id, source['name']))
    connection.execute("DELETE FROM sync_state WHERE target_type = 'author' AND target_id IN (?, ?)",
                       (author_id, new_id))
    return new_id
