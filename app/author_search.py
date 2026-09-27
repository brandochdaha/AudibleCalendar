"""Presentation-level grouping for author catalogue search results."""

from __future__ import annotations

from typing import Any

from .author_identity import author_display_names, author_identities_can_merge, names_likely_same
from .author_decisions import decision_key
from .author_sources import identities_separate, source_asins
from .normalization import is_followable_author, person_name_key, series_title_key


# Confirmed by the user; presentation grouping does not merge stored rows.
_CONFIRMED_SEARCH_GROUPS = (
    {"B01M14OOT8", "name:maxime-j-durand", "B09PNWS2L6", "name:void-herald"},
)


def _identity_asins(author: dict[str, Any]) -> set[str]:
    return source_asins(author)


def _book_asins(author: dict[str, Any], books_by_author: dict[str, set[str]]) -> set[str]:
    books: set[str] = set()
    for asin in _identity_asins(author):
        books.update(str(book_asin) for book_asin in books_by_author.get(asin, set()) if book_asin)
    return books


def _series_keys(author: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for series in [*(author.get("series") or []), *(author.get("selected_series") or [])]:
        if not isinstance(series, dict):
            continue
        asin = str(series.get("asin") or "").strip()
        name = series_title_key(series.get("name") or series.get("title"))
        if asin:
            result.add(f"asin:{asin}")
        elif name:
            # Do not count a stable series twice (once by ASIN and once by
            # name). That would make two shared series look like four.
            result.add(f"name:{name}")
    return result


def _stored_pair_decision(
    left: dict[str, Any],
    right: dict[str, Any],
    decisions: dict[tuple[str, str, str], str],
) -> str | None:
    marketplace = left.get("marketplace") or right.get("marketplace")
    values = {
        decisions.get(decision_key(marketplace, left_asin, right_asin))
        for left_asin in source_asins(left)
        for right_asin in source_asins(right)
    }
    if "separate" in values:
        return "separate"
    if "merge" in values:
        return "merge"
    return None


def _pair_evidence(
    left: dict[str, Any],
    right: dict[str, Any],
    books_by_author: dict[str, set[str]],
    decisions: dict[tuple[str, str, str], str] | None = None,
) -> set[str]:
    stored_decision = _stored_pair_decision(left, right, decisions or {})
    if stored_decision == "separate":
        return set()
    if stored_decision == "merge":
        return {"user decision"}
    if not is_followable_author(left) or not is_followable_author(right):
        return set()
    if identities_separate(left, right):
        return set()
    if any(source_asins(left) & group and source_asins(right) & group
           for group in _CONFIRMED_SEARCH_GROUPS):
        return {"confirmed aliases"}
    if author_identities_can_merge(left, right):
        return {"known identity"}

    left_books = _book_asins(left, books_by_author)
    right_books = _book_asins(right, books_by_author)
    left_series = _series_keys(left)
    right_series = _series_keys(right)
    shared_books = len(left_books & right_books)
    shared_series = len(left_series & right_series)
    evidence: set[str] = set()

    # Name variants need catalogue evidence as well. This covers middle
    # initials and name-only Audible identities without treating names alone
    # as proof of ownership. A shared series is a strong enough catalogue
    # signal because series membership is author-scoped in the search data.
    if names_likely_same(left.get("name", ""), right.get("name", "")):
        if shared_books >= 2 or shared_series >= 1:
            evidence.add("matching names with overlapping catalog")

    # Shared catalogue entries can be collaborations. Different names need
    # confirmed identity evidence, regardless of how much their books overlap.
    return evidence


def _member_payload(author: dict[str, Any]) -> dict[str, Any]:
    member = dict(author)
    member.pop("author_group_members", None)
    member.pop("author_group_aliases", None)
    member.pop("author_group_evidence", None)
    return member


def _series_by_key(author: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for series in [*(author.get("series") or []), *(author.get("selected_series") or [])]:
        if not isinstance(series, dict):
            continue
        asin = str(series.get("asin") or "").strip()
        name = str(series.get("name") or series.get("title") or "").strip()
        key = f"asin:{asin}" if asin else f"name:{series_title_key(name)}"
        if key == "name:":
            continue
        current = result.setdefault(key, {})
        for field, value in series.items():
            if current.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                current[field] = value
    return result


def _shared_series(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    left_series = _series_by_key(left)
    right_series = _series_by_key(right)
    shared: list[dict[str, Any]] = []
    for key in sorted(left_series.keys() & right_series.keys()):
        series = dict(left_series[key])
        for field, value in right_series[key].items():
            if series.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                series[field] = value
        shared.append(series)
    return sorted(
        shared,
        key=lambda item: str(item.get("name") or item.get("title") or "").casefold(),
    )


def _candidate_member(
    author: dict[str, Any],
    books_by_author: dict[str, set[str]],
) -> dict[str, Any]:
    member = _member_payload(author)
    member["book_count"] = len(_book_asins(author, books_by_author))
    member["series_count"] = len(_series_keys(author))
    member["source_asins"] = sorted(source_asins(author))
    member["source_groups"] = [
        sorted(source_asins(item))
        for item in author.get("author_group_members") or [author]
    ]
    return member


def _candidate_records(authors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        # A displayed group is one side of a suggestion, not several pairs.
        records.append(dict(author))
    return records


def author_merge_candidates(
    authors: list[dict[str, Any]],
    books_by_author: dict[str, set[str]] | None = None,
    decisions: dict[tuple[str, str, str], str] | None = None,
    *,
    include_separate: bool = False,
    min_shared_series: int = 1,
) -> list[dict[str, Any]]:
    """Suggest unconfirmed author pairs that share at least one series.

    This deliberately errs toward review. Different names can be co-authors,
    so they are offered as a user decision rather than being grouped
    automatically. Formatting-only name variants are already handled by
    ``group_search_author_records`` and do not need a second card.
    """

    books_by_author = books_by_author or {}
    decisions = decisions or {}
    min_shared_series = max(1, int(min_shared_series))
    records = _candidate_records(authors)
    candidates: list[dict[str, Any]] = []
    for right_index in range(1, len(records)):
        for left_index in range(right_index):
            left = records[left_index]
            right = records[right_index]
            # Role-labelled contributors can share an entire series with its
            # writer without being an alias or pen name. They are not authors
            # the user can follow, so do not offer them as author matches.
            if not is_followable_author(left) or not is_followable_author(right):
                continue
            stored_decision = _stored_pair_decision(left, right, decisions)
            if stored_decision == "merge" or (
                stored_decision == "separate" and not include_separate
            ):
                continue
            if (
                (identities_separate(left, right)
                 and not (include_separate and stored_decision == "separate"))
                or author_identities_can_merge(left, right)
            ):
                continue
            if (
                names_likely_same(left.get("name", ""), right.get("name", ""))
                and not (include_separate and stored_decision == "separate")
            ):
                continue
            shared_series = _shared_series(left, right)
            if (
                len(shared_series) < min_shared_series
                and not (include_separate and stored_decision == "separate")
            ):
                continue

            members = [
                _candidate_member(left, books_by_author),
                _candidate_member(right, books_by_author),
            ]
            members.sort(key=lambda member: (
                -int(member.get("book_count") or 0),
                -int(member.get("series_count") or 0),
                str(member.get("name") or "").casefold(),
                str(member.get("asin") or ""),
            ))
            merged = _merge_group(
                members,
                books_by_author,
                decisions,
                primary_asin=str(members[0].get("asin") or ""),
            )
            asins = [str(member.get("asin") or "") for member in members]
            candidates.append({
                "kind": "author-match",
                "asin": "match:" + "|".join(sorted(asin for asin in asins if asin)),
                "name": str(members[0].get("name") or "Possible author match"),
                "marketplace": members[0].get("marketplace") or members[1].get("marketplace"),
                "author_match": True,
                "members": members,
                "primary_asin": members[0].get("asin"),
                "shared_series": shared_series,
                "shared_series_count": len(shared_series),
                "merged_result": merged,
                "decision": stored_decision,
            })
    return candidates


def _merge_series(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for member in members:
        for series in member.get("series") or []:
            if not isinstance(series, dict):
                continue
            asin = str(series.get("asin") or "").strip()
            key = f"asin:{asin}" if asin else f"name:{series_title_key(series.get('name') or series.get('title'))}"
            if key == "name:":
                continue
            current = merged.setdefault(key, {})
            for field, value in series.items():
                if current.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                    current[field] = value
    return list(merged.values())


def _merge_related_items(members: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for member in members:
        for item in member.get(field) or []:
            if not isinstance(item, dict):
                continue
            asin = str(item.get("asin") or "").strip()
            name = person_name_key(item.get("name") or item.get("title"))
            key = f"asin:{asin}" if asin else f"name:{name}"
            if key == "name:":
                continue
            current = merged.setdefault(key, {})
            for item_field, value in item.items():
                if current.get(item_field) in (None, "", [], {}) and value not in (None, "", [], {}):
                    current[item_field] = value
    return list(merged.values())


def _merge_source_identities(members: list[dict[str, Any]]) -> list[dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    for member in members:
        sources = member.get("source_identities") or []
        if member.get("asin"):
            sources = [{"asin": member["asin"], "name": member.get("name")}, *sources]
        for source in sources:
            if not isinstance(source, dict) or not source.get("asin"):
                continue
            asin = str(source["asin"])
            identities.setdefault(asin, {"asin": asin, "name": str(source.get("name") or member.get("name") or "")})
    return list(identities.values())


def _merge_group(
    members: list[dict[str, Any]],
    books_by_author: dict[str, set[str]],
    decisions: dict[tuple[str, str, str], str] | None = None,
    primary_asin: str | None = None,
) -> dict[str, Any]:
    stable = [member for member in members if not str(member.get("asin") or "").startswith("name:")]
    selected = next((member for member in members if str(member.get("asin") or "") == primary_asin), None)
    primary = dict(selected or (stable[0] if stable else members[0]))
    member_payloads = [_member_payload(member) for member in members]
    source_identities = _merge_source_identities(members)
    source_asin_values = [source["asin"] for source in source_identities]

    names: list[str] = []
    for member in members:
        names.extend([str(member.get("name") or ""), *(member.get("aliases") or [])])
        names.extend(str(source.get("name") or "") for source in member.get("source_identities") or [])
    display_names = author_display_names(names)
    primary_name_key = person_name_key(primary.get("name"))
    aliases = [name for name in display_names if person_name_key(name) != primary_name_key]

    primary["series"] = _merge_series(members)
    primary["selected_series"] = _merge_related_items(members, "selected_series")
    primary["selected_books"] = _merge_related_items(members, "selected_books")
    primary["source_asins"] = source_asin_values
    primary["source_identities"] = source_identities
    primary["author_group"] = True
    primary["author_group_members"] = member_payloads
    primary["author_group_member_count"] = len(member_payloads)
    primary["author_group_aliases"] = aliases
    primary["author_group_evidence"] = sorted({
        evidence
        for index, left in enumerate(members)
        for right in members[index + 1:]
        for evidence in _pair_evidence(left, right, books_by_author, decisions)
    })
    if any(member.get("relationships_incomplete") for member in members):
        primary["relationships_incomplete"] = True
    for key in ("is_followed", "is_following_series", "is_tracked", "follow_all", "is_favourite"):
        if any(member.get(key) for member in members):
            primary[key] = True
    return primary


def group_search_author_records(
    authors: list[dict[str, Any]],
    books_by_author: dict[str, set[str]] | None = None,
    decisions: dict[tuple[str, str, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Group strongly evidenced search aliases without changing stored identities."""

    books_by_author = books_by_author or {}
    decisions = decisions or {}
    records: list[dict[str, Any]] = []
    for author in authors:
        if not isinstance(author, dict):
            continue
        nested = author.get("author_group_members")
        if nested:
            records.extend(_member_payload(member) for member in nested if isinstance(member, dict))
        else:
            records.append(dict(author))
    if len(records) < 2:
        return records

    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def members(root: int) -> list[dict[str, Any]]:
        return [record for index, record in enumerate(records) if find(index) == root]

    for right_index in range(1, len(records)):
        for left_index in range(right_index):
            if not _pair_evidence(records[left_index], records[right_index], books_by_author, decisions):
                continue
            left_root = find(left_index)
            right_root = find(right_index)
            if left_root == right_root:
                continue
            if all(
                _pair_evidence(left, right, books_by_author, decisions)
                for left in members(left_root)
                for right in members(right_root)
            ):
                parent[right_root] = left_root

    grouped: list[dict[str, Any]] = []
    seen_roots: set[int] = set()
    for index in range(len(records)):
        root = find(index)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        group = members(root)
        grouped.append(_merge_group(group, books_by_author, decisions) if len(group) > 1 else group[0])
    return grouped
