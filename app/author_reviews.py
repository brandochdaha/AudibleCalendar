"""Author identity reviews for directly followed authors."""

from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import json

from .author_decisions import decision_key, read_decisions, record_decision
from .author_evidence import ensure_identity
from .author_identity import _identity_groups, consolidate_authors, names_likely_same
from .author_mutations import _merge_author_rows, split_author_sources
from .sync_runtime import _read_sync_runtime, _write_sync_runtime


def _source_asins(sources):
    return sorted(source['asin'] for source in sources)


def _book_details(book_ids, catalogue):
    return [
        {**catalogue[book], 'series': sorted(catalogue[book]['series'].values(),
                                           key=lambda item: (item['name'].casefold(), item['asin']))}
        for book in sorted(book_ids, key=lambda book: (catalogue[book]['title'].casefold(), book))
    ]


def _review(left, right, books, titles, kind):
    left_books = set().union(*(books.get(source['asin'], set()) for source in left))
    right_books = set().union(*(books.get(source['asin'], set()) for source in right))
    shared = left_books & right_books
    identity = [kind, left[0]['marketplace'],
                [(source['asin'], source['name']) for source in left],
                [(source['asin'], source['name']) for source in right],
                sorted(left_books), sorted(right_books)]
    return {
        'id': sha256(json.dumps(identity).encode()).hexdigest(),
        'kind': kind,
        'left': [{'asin': source['asin'], 'name': source['name']} for source in left],
        'right': [{'asin': source['asin'], 'name': source['name']} for source in right],
        'shared_books': len(shared),
        'shared_titles': [titles[book]['title'] for book in sorted(shared)[:3]],
        'shared_book_details': _book_details(shared, titles),
        'left_only_book_details': _book_details(left_books - right_books, titles),
        'right_only_book_details': _book_details(right_books - left_books, titles),
        'left_only_books': len(left_books - right_books),
        'right_only_books': len(right_books - left_books),
    }


def _pending_reviews(connection, marketplace):
    followed_authors = {row['id'] for row in connection.execute('''
        SELECT id FROM authors WHERE marketplace = ? AND is_tracked = 1
    ''', (marketplace,))}
    if not followed_authors:
        return []
    sources = [dict(row) for row in connection.execute('''
        SELECT s.*, a.asin AS primary_asin FROM author_sources s
        JOIN authors a ON a.id = s.author_id WHERE s.marketplace = ? ORDER BY s.id
    ''', (marketplace,)) if row['author_id'] in followed_authors]
    by_author = defaultdict(list)
    for source in sources:
        by_author[source['author_id']].append(source)
    books, titles, contributors = defaultdict(set), {}, defaultdict(set)
    for row in connection.execute('''SELECT s.asin, s.author_id, b.id, b.title, b.asin AS book_asin,
        series.id AS series_id, series.asin AS series_asin, series.name AS series_name
        FROM author_source_books sb JOIN author_sources s ON s.id = sb.source_id
        JOIN books b ON b.id = sb.book_id
        LEFT JOIN book_series bs ON bs.book_id = b.id
        LEFT JOIN series ON series.id = bs.series_id AND series.marketplace = b.marketplace
        WHERE s.marketplace = ? AND b.marketplace = ?''', (marketplace, marketplace)):
        if row['author_id'] not in followed_authors:
            continue
        books[row['asin']].add(row['id'])
        book = titles.setdefault(row['id'], {
            'asin': row['book_asin'], 'title': row['title'], 'series': {},
        })
        if row['series_id'] is not None:
            book['series'][row['series_id']] = {'asin': row['series_asin'], 'name': row['series_name']}
        contributors[row['id']].add(row['author_id'])
    decisions = read_decisions(connection)
    reviews = []
    # Preserve existing ownership until a user confirms a split. Review one
    # uncertain component at a time so each choice has an unambiguous result.
    doubtful = set()
    for author_id, members in by_author.items():
        groups = _identity_groups(members, books, decisions, preserve_existing=False)
        if len(groups) < 2:
            continue
        right = next(group for group in groups
                     if members[0]['primary_asin'] not in _source_asins(group))
        right_asins = set(_source_asins(right))
        left = [source for source in members if source['asin'] not in right_asins]
        reviews.append(_review(left, right, books, titles, 'split'))
        doubtful.add(author_id)
    # Only authors with shared books are candidates. The inverted book index
    # avoids comparing every author in a potentially large cached catalogue.
    candidates = set()
    for authors in contributors.values():
        candidates.update(combinations(sorted(authors), 2))
    for left_id, right_id in sorted(candidates):
        if left_id in doubtful or right_id in doubtful:
            continue
        left, right = by_author[left_id], by_author[right_id]
        if any(decisions.get(decision_key(marketplace, a['asin'], b['asin'])) == 'separate'
               for a in left for b in right):
            continue
        review = _review(left, right, books, titles, 'merge')
        similar_names = any(names_likely_same(a['name'], b['name']) for a in left for b in right)
        identical_catalogues = (review['shared_books'] >= 2
                                and not review['left_only_books'] and not review['right_only_books'])
        if similar_names or identical_catalogues:
            reviews.append(review)
    return reviews


def list_author_reviews(db, marketplace):
    with db.identity_lock:
        consolidate_authors(db)
        with db.transaction() as connection:
            connection.execute('BEGIN')
            return _pending_reviews(connection, marketplace)


def _finish_identity_change(connection, marketplace):
    # Display snapshots contain canonical IDs and follow state. Raw upstream
    # caches remain intact; rebuilding these uses cached data.
    connection.execute('''DELETE FROM source_cache WHERE operation = 'detail'
        AND json_valid(cache_key) AND json_extract(cache_key, '$.parameters.region') = ?''',
        (marketplace,))
    runtime = _read_sync_runtime(connection)
    runtime['catalogue_revision'] += 1
    _write_sync_runtime(connection, runtime)


def decide_author_review(db, marketplace, review_id, decision):
    if decision not in {'merge', 'separate'}:
        raise ValueError('Choose merge or separate')
    with db.identity_lock:
        consolidate_authors(db)
        with db.transaction() as connection:
            connection.execute('BEGIN IMMEDIATE')
            review = next((item for item in _pending_reviews(connection, marketplace)
                           if item['id'] == review_id), None)
            if review is None:
                raise ValueError('This author match has changed. Reload the reviews before deciding.')
            left, right = _source_asins(review['left']), _source_asins(review['right'])
            record_decision(connection, marketplace, left, right, decision)
            owners = {row['asin']: row['author_id'] for row in connection.execute(
                'SELECT asin, author_id FROM author_sources WHERE marketplace = ?', (marketplace,))}
            if review['kind'] == 'merge' and decision == 'merge':
                _merge_author_rows(connection, owners[left[0]], owners[right[0]])
            elif review['kind'] == 'split' and decision == 'separate':
                split_author_sources(connection, owners[left[0]], set(right))
            _finish_identity_change(connection, marketplace)
        db.author_identities_dirty = False


def _search_member_values(member):
    if isinstance(member, dict):
        return str(member.get('asin') or '').strip(), str(member.get('name') or '').strip()
    return str(getattr(member, 'asin', '') or '').strip(), str(getattr(member, 'name', '') or '').strip()


def _source_group(connection, marketplace, requested_asin):
    owner = connection.execute(
        'SELECT author_id FROM author_sources WHERE marketplace = ? AND asin = ?',
        (marketplace, requested_asin),
    ).fetchone()
    if owner is None:
        return [requested_asin], None
    return [row['asin'] for row in connection.execute(
        'SELECT asin FROM author_sources WHERE author_id = ? ORDER BY id',
        (owner['author_id'],),
    )], owner['author_id']


def decide_author_search_match(db, marketplace, primary_asin, members, decision, *, manual=False, override_separation=False):
    """Persist a merge/separation chosen from an actionable search result."""
    if decision not in {'merge', 'separate'}:
        raise ValueError('Choose merge or separate')
    values = [_search_member_values(member) for member in members]
    if len(values) != 2 or any(not asin or not name for asin, name in values):
        raise ValueError('This author match is incomplete. Search again before deciding.')
    asins = [asin for asin, _name in values]
    if len(set(asins)) != len(asins):
        raise ValueError('Choose two different author identities.')
    primary_asin = str(primary_asin or '').strip()
    if primary_asin not in asins:
        raise ValueError('This author match has changed. Search again before deciding.')

    with db.identity_lock:
        consolidate_authors(db)
        with db.transaction() as connection:
            connection.execute('BEGIN IMMEDIATE')
            groups = []
            owner_ids = []
            for member, (asin, name) in zip(members, values):
                expected = (member.get('source_asins') if isinstance(member, dict)
                            else getattr(member, 'source_asins', None))
                snapshots = (member.get('source_groups') if isinstance(member, dict)
                             else getattr(member, 'source_groups', None))
                snapshots = snapshots or [expected or [asin]]
                group = set()
                for snapshot in snapshots:
                    if not snapshot or group & set(snapshot):
                        raise ValueError('This author match has changed. Reload before deciding.')
                    actual, _ = _source_group(connection, marketplace, snapshot[0])
                    if set(actual) != set(snapshot):
                        raise ValueError('This author match has changed. Reload before deciding.')
                    group.update(actual)
                if asin not in group or group != set(expected or [asin]):
                    raise ValueError('This author match has changed. Reload before deciding.')
                for source_asin in group:
                    ensure_identity(connection, marketplace, source_asin, name if source_asin == asin else source_asin)
                    _, owner_id = _source_group(connection, marketplace, source_asin)
                    owner_ids.append(owner_id)
                groups.append(sorted(group))
            if set(groups[0]) & set(groups[1]):
                raise ValueError('These identities are already merged. Reload before deciding.')
            if manual and decision == 'merge' and not override_separation:
                decisions = read_decisions(connection)
                if any(decisions.get(decision_key(marketplace, left, right)) == 'separate'
                       for left in groups[0] for right in groups[1]):
                    raise ValueError('These authors were marked separate. Reload and review that decision before overriding it.')
            record_decision(connection, marketplace, groups[0], groups[1], decision)

            distinct_owners = {owner_id for owner_id in owner_ids if owner_id is not None}
            if decision == 'merge' and len(distinct_owners) > 1:
                _, canonical_id = _source_group(connection, marketplace, primary_asin)
                if canonical_id is None:
                    canonical_id = min(distinct_owners)
                for duplicate_id in sorted(distinct_owners - {canonical_id}):
                    _merge_author_rows(connection, canonical_id, duplicate_id)
            _finish_identity_change(connection, marketplace)
        db.author_identities_dirty = False


def split_author_management_group(db, marketplace, primary_asin, split_asins):
    """Split selected source identities from a canonical author group."""
    primary_asin = str(primary_asin or '').strip()
    selected_asins = {str(asin or '').strip() for asin in split_asins if str(asin or '').strip()}
    if not primary_asin or not selected_asins:
        raise ValueError('Choose at least one merged identity to split.')
    if primary_asin in selected_asins:
        raise ValueError('The primary author identity must remain in the original group.')

    with db.identity_lock:
        consolidate_authors(db)
        with db.transaction() as connection:
            connection.execute('BEGIN IMMEDIATE')
            owner = connection.execute(
                'SELECT author_id FROM author_sources WHERE marketplace = ? AND asin = ?',
                (marketplace, primary_asin),
            ).fetchone()
            if owner is None:
                raise ValueError('This merged author has changed. Reload author management before splitting it.')
            sources = [row['asin'] for row in connection.execute(
                'SELECT asin FROM author_sources WHERE author_id = ? ORDER BY id',
                (owner['author_id'],),
            )]
            available = set(sources)
            if len(available) < 2 or not selected_asins <= available:
                raise ValueError('This merged author has changed. Reload author management before splitting it.')
            remaining_asins = available - selected_asins
            if primary_asin not in remaining_asins:
                raise ValueError('The primary author identity must remain in the original group.')
            record_decision(
                connection,
                marketplace,
                sorted(remaining_asins),
                sorted(selected_asins),
                'separate',
            )
            split_author_sources(connection, owner['author_id'], selected_asins)
            _finish_identity_change(connection, marketplace)
        db.author_identities_dirty = False
