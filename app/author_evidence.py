"""Retain search evidence without following authors or importing partial books."""

import json

from .normalization import series_title_key


def ensure_identity(connection, marketplace, asin, name):
    # Do not replace richer source payloads, preferences or canonical names.
    from .repository.named import _upsert_named

    existing = connection.execute(
        'SELECT author_id, name FROM author_sources WHERE marketplace = ? AND asin = ?',
        (marketplace, asin),
    ).fetchone()
    if not existing:
        _upsert_named(connection, 'authors', {'asin': asin, 'name': name}, marketplace)
    elif existing['name'] == asin and name != asin:
        # Old search decisions saved only IDs. Replace their honest ID labels
        # when a later search supplies the real catalogue name.
        connection.execute('UPDATE author_sources SET name = ? WHERE marketplace = ? AND asin = ?',
                           (name, marketplace, asin))
        connection.execute('UPDATE authors SET name = ? WHERE id = ? AND asin = ? AND name = ?',
                           (name, existing['author_id'], asin, asin))


def restore_decision_identities(connection, decisions):
    for marketplace, left, right in decisions:
        for asin in (left, right):
            ensure_identity(connection, marketplace, asin, asin)


def series_key(series):
    return ('asin:' + str(series['asin']) if series.get('asin')
            else 'name:' + series_title_key(series.get('name') or series.get('title') or ''))


def remember_author_search(db, marketplace, authors, books_by_author):
    with db.identity_lock, db.transaction() as connection:
        for author in authors:
            asin, name = author.get('asin'), author.get('name')
            if not asin or not name:
                continue
            ensure_identity(connection, marketplace, asin, name)
            previous = connection.execute(
                'SELECT * FROM author_search_evidence WHERE marketplace = ? AND asin = ?',
                (marketplace, asin),
            ).fetchone()
            books = set(json.loads(previous['book_asins'])) if previous else set()
            books.update(books_by_author.get(asin, set()))
            series = {series_key(item): item for item in json.loads(previous['series'])} if previous else {}
            series.update({series_key(item): item for item in author.get('series') or []})
            connection.execute('''INSERT INTO author_search_evidence VALUES (?, ?, ?, ?)
                ON CONFLICT(marketplace, asin) DO UPDATE SET
                book_asins=excluded.book_asins, series=excluded.series''',
                (marketplace, asin, json.dumps(sorted(books)), json.dumps(list(series.values()))))
        db.author_identities_dirty = True


def add_search_evidence(connection, marketplace, books_by_source, series_by_source):
    for row in connection.execute(
        'SELECT * FROM author_search_evidence WHERE marketplace = ?', (marketplace,),
    ):
        books_by_source.setdefault(row['asin'], set()).update(json.loads(row['book_asins']))
        series_by_source.setdefault(row['asin'], {}).update({
            series_key(item): item for item in json.loads(row['series'])
        })
