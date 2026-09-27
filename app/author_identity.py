"""Local author identity consolidation and alias preservation."""

from __future__ import annotations

from typing import Any

from .author_aliases import confirmed_author_match, confirmed_identity_key
from .author_sources import identities_separate, record_author_source, source_asins
from .author_decisions import decision_key, read_decisions
from .author_mutations import _merge_author_rows
from .db import Database
from .normalization import person_name_fingerprint, person_name_key


def _name_without_middle_initials(value: str) -> str:
    tokens = person_name_key(value).split()
    if len(tokens) < 3:
        return " ".join(tokens)
    return " ".join(token for token in tokens if len(token) != 1)


def names_likely_same(left: str, right: str) -> bool:
    """Find similar names for review, including differing middle initials."""

    left_key = person_name_key(left)
    right_key = person_name_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    left_fingerprint = person_name_fingerprint(left)
    right_fingerprint = person_name_fingerprint(right)
    if left_fingerprint and left_fingerprint == right_fingerprint:
        return True
    return _name_without_middle_initials(left) == _name_without_middle_initials(right)


def author_identities_can_merge(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Match established identities; names alone do not establish ownership."""

    if identities_separate(left, right):
        return False
    if source_asins(left) & source_asins(right):
        return True
    if left.get("canonical_id") is not None and right.get("canonical_id") is not None:
        return left["canonical_id"] == right["canonical_id"]
    if confirmed_author_match(left, right):
        return True
    return False


def names_cosmetic(left, right):
    """Ignore punctuation, spacing and repeated credits, not differing initials."""
    key = person_name_fingerprint(left)
    return bool(key and key == person_name_fingerprint(right))


def author_display_names(names: list[str]) -> list[str]:
    """Keep genuine aliases visible while hiding formatting-only variants."""

    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        value = str(name or "").strip()
        key = value.casefold()
        if value and key not in seen:
            unique.append(value)
            seen.add(key)
    if len(unique) < 2:
        return unique
    primary_fingerprint = person_name_fingerprint(unique[0])
    visible = [unique[0]]
    for name in unique[1:]:
        if person_name_fingerprint(name) != primary_fingerprint:
            visible.append(name)
    return visible


def _identity_groups(records, books_by_author, decisions=None, *, preserve_existing=True):
    """Merge only direct evidence; leave ambiguous aliases for a human decision."""
    decisions = decisions or {}
    parent = list(range(len(records)))
    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    def union(left, right):
        left_members = [record for i, record in enumerate(records) if find(i) == find(left)]
        right_members = [record for i, record in enumerate(records) if find(i) == find(right)]
        if any(blocked(a, b) for a in left_members for b in right_members):
            return
        parent[find(right)] = find(left)
    def decision(left, right):
        return decisions.get(decision_key(left.get("marketplace"), left.get("asin", ""), right.get("asin", "")))
    def blocked(left, right):
        return (identities_separate(left, right) or decision(left, right) == "separate"
                or (left.get("canonical_id") is not None and right.get("canonical_id") is not None
                    and left["canonical_id"] != right["canonical_id"]))
    def direct_match(left, right):
        if blocked(left, right):
            return False
        if author_identities_can_merge(left, right) or decision(left, right) == "merge":
            return True
        return (left.get("marketplace") == right.get("marketplace")
                and names_cosmetic(left.get("name"), right.get("name"))
                and len(books_by_author.get(str(left.get("asin")), set())
                        & books_by_author.get(str(right.get("asin")), set())) >= 2)
    stable = {}
    stable_by_asin = {}
    for index, record in enumerate(records):
        asin = str(record.get("asin") or "")
        stable[index] = bool(asin and not asin.startswith("name:"))
        if stable[index]:
            key = (record.get("marketplace"), asin)
            if key in stable_by_asin:
                union(stable_by_asin[key], index)
            else:
                stable_by_asin[key] = index
    # Existing source mappings are established identities. New stable IDs
    # require direct evidence against every stable member, not a bridge.
    known = {}
    for index, record in enumerate(records):
        key = record.get("author_id") if preserve_existing else None
        if key is not None:
            if key in known:
                union(known[key], index)
            else:
                known[key] = index
    buckets = {}
    for index, record in enumerate(records):
        if stable[index]:
            key = (record.get("marketplace"), person_name_fingerprint(record.get("name")))
            buckets.setdefault(key, []).append(index)
            confirmed = confirmed_identity_key(record)
            if confirmed is not None:
                buckets.setdefault((record.get("marketplace"), confirmed), []).append(index)
    by_source = {(record.get("marketplace"), record.get("asin")): i for i, record in enumerate(records)}
    for (marketplace, left, right), value in decisions.items():
        if value == "merge" and (marketplace, left) in by_source and (marketplace, right) in by_source:
            union(by_source[(marketplace, left)], by_source[(marketplace, right)])
    for indices in buckets.values():
        for position, index in enumerate(indices):
            for other in indices[position + 1:]:
                if find(index) == find(other):
                    continue
                left = [records[i] for i in range(len(records)) if stable[i] and find(i) == find(index)]
                right = [records[i] for i in range(len(records)) if stable[i] and find(i) == find(other)]
                if all(direct_match(a, b) for a in left for b in right):
                    union(index, other)
    links = []
    for index, left in enumerate(records):
        if stable[index]:
            continue
        # A name-only source already attached to a stable identity cannot
        # bridge that identity to another stable author on its own.
        if any(stable[i] and find(i) == find(index) for i in range(len(records))):
            continue
        for other, right in enumerate(records):
            if index == other or (not stable[other] and other < index):
                continue
            if left.get("marketplace") != right.get("marketplace"):
                continue
            if not direct_match(left, right):
                continue
            if stable[other]:
                links.append((index, other))
            else:
                union(index, other)
    candidates = {}
    for generated, identified in links:
        candidates.setdefault(find(generated), set()).add(find(identified))
    for generated, identified in candidates.items():
        if len(identified) == 1:
            union(next(iter(identified)), generated)
    groups = {}
    for index, record in enumerate(records):
        groups.setdefault(find(index), []).append(record)
    return list(groups.values())


def collapse_author_records(
    authors: list[dict[str, Any]],
    books_by_author: dict[str, set[str]] | None = None,
    decisions: dict[tuple[str, str, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Collapse established identities and directly evidenced duplicates."""

    records = [dict(author) for author in authors if isinstance(author, dict) and author.get("name")]
    if len(records) < 2:
        return records
    collapsed: list[dict[str, Any]] = []
    for members in _identity_groups(records, books_by_author or {}, decisions):
        stable = [record for record in members if not str(record.get("asin") or "").startswith("name:")]
        primary = dict(stable[0] if stable else members[0])
        identities = {str(member["asin"]): member.get("name") for member in members if member.get("asin")}
        for member in members:
            for source in member.get("source_identities") or []:
                identities[str(source["asin"])] = source.get("name")
        if len([asin for asin in identities if not asin.startswith("name:")]) > 1:
            primary["source_asins"] = list(identities)
            primary["source_identities"] = [{"asin": asin, "name": name} for asin, name in identities.items()]
        names: list[str] = []
        for member in members:
            names.extend([str(member.get("name") or ""), *(member.get("aliases") or [])])
        # Copy non-identity fields before rebuilding aliases/display_name. A
        # stale payload can carry a display_name for one of the duplicate
        # rows; it must not re-introduce that formatting-only value below.
        for member in members:
            if member.get("series"):
                series = {item["asin"]: item for item in primary.get("series") or []}
                series.update({item["asin"]: item for item in member["series"]})
                primary["series"] = list(series.values())
            for key, value in member.items():
                if key in {"name", "asin", "aliases", "display_name"}:
                    continue
                if primary.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                    primary[key] = value
            for key in ("is_followed", "is_following_series", "is_tracked", "follow_all", "is_favourite", "relationships_incomplete"):
                if member.get(key):
                    primary[key] = True
        blocked_sources = {asin for member in members for asin in member.get("separate_source_asins") or []}
        if blocked_sources:
            primary["separate_source_asins"] = sorted(blocked_sources)

        aliases: list[str] = []
        seen_aliases = {str(primary.get("name") or "").casefold()}
        for name in names:
            value = str(name or "").strip()
            if value and value.casefold() not in seen_aliases:
                aliases.append(value)
                seen_aliases.add(value.casefold())
        if aliases:
            primary["aliases"] = aliases
        display_names = author_display_names([str(primary.get("name") or ""), *aliases])
        if len(display_names) > 1:
            primary["display_name"] = " / ".join(display_names)
        else:
            primary.pop("display_name", None)
        collapsed.append(primary)
    return collapsed


def consolidate_authors(db: Database) -> int:
    """Merge confident stable/name-only duplicates while preserving aliases."""
    if not db.author_identities_dirty:
        return 0
    with db.identity_lock:
        if not db.author_identities_dirty:
            return 0
        merged = _consolidate_authors(db)
        db.author_identities_dirty = False
        return merged


def canonicalize_author_records(authors, local_authors, books_by_author=None, decisions=None):
    """Use persisted source ownership before grouping catalogue display records."""
    by_source = {asin: author for author in local_authors for asin in source_asins(author)}
    result = []
    for raw in authors:
        if not isinstance(raw, dict):
            continue
        author = dict(raw)
        local = by_source.get(str(author.get("asin") or ""))
        if local is None:
            matches = [item for item in local_authors if author_identities_can_merge(author, item)]
            local = matches[0] if len(matches) == 1 else None
        if local:
            for key in ("asin", "name", "canonical_id", "source_asins", "source_identities",
                        "separate_source_asins", "aliases", "display_name"):
                author.pop(key, None)
                if key in local:
                    author[key] = local[key]
        result.append(author)
    return collapse_author_records(result, books_by_author, decisions)


def _consolidate_authors(db: Database) -> int:
    from .author_evidence import restore_decision_identities

    merged_count = 0
    with db.transaction() as connection:
        decisions = read_decisions(connection)
        restore_decision_identities(connection, decisions)
        # Include authors inserted by demo data or a marketplace move. Normal
        # catalogue writes register sources as part of their own transaction.
        rows = connection.execute("""
            SELECT s.id AS source_id, a.id AS author_id, a.marketplace,
                   COALESCE(s.asin, a.asin) AS asin, COALESCE(s.name, a.name) AS name
            FROM authors a LEFT JOIN author_sources s ON s.author_id = a.id
            ORDER BY a.id, s.id
        """).fetchall()
        records = [dict(row) for row in rows]
        for record in records:
            if record["source_id"] is None:
                record_author_source(connection, record["author_id"], record["marketplace"], record)
                record["source_id"] = connection.execute(
                    "SELECT id FROM author_sources WHERE marketplace = ? AND asin = ?",
                    (record["marketplace"], record["asin"]),
                ).fetchone()[0]
                connection.execute("""INSERT OR IGNORE INTO author_source_books (source_id, book_id)
                    SELECT ?, book_id FROM book_authors WHERE author_id = ?""",
                    (record["source_id"], record["author_id"]))
        name_keys = [(row["marketplace"], person_name_fingerprint(row["name"])) for row in records]
        confirmed_keys = [(row["marketplace"], confirmed_identity_key(row)) for row in records
                          if confirmed_identity_key(row) is not None]
        if (not any(str(row["asin"]).startswith("name:") for row in records)
                and len(set(name_keys)) == len(records)
                and len(set(confirmed_keys)) == len(confirmed_keys) and not decisions):
            return 0
        source_books = {}
        for row in connection.execute("SELECT source_id, book_id FROM author_source_books"):
            source_books.setdefault(row["source_id"], set()).add(row["book_id"])
        grouped = {}
        for record in records:
            grouped.setdefault(record["marketplace"], []).append(record)
        for marketplace_rows in grouped.values():
            catalogue = {row["asin"]: source_books.get(row["source_id"], set()) for row in marketplace_rows}
            for members in _identity_groups(marketplace_rows, catalogue, decisions):
                author_ids = {member["author_id"] for member in members}
                if len(author_ids) < 2:
                    continue
                stable = [member for member in members if not str(member["asin"]).startswith("name:")]
                canonical_id = min(member["author_id"] for member in (stable or members))
                for duplicate_id in sorted(author_ids - {canonical_id}):
                    _merge_author_rows(connection, canonical_id, duplicate_id)
                    merged_count += 1
    return merged_count
