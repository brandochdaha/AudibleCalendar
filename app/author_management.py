"""Catalogue-wide author identity management data."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .author_decisions import read_decisions
from .author_evidence import add_search_evidence
from .author_identity import consolidate_authors
from .author_search import author_merge_candidates
from .author_sources import source_asins
from .normalization import series_title_key
from .repository import list_authors


def _relation_data(connection: Any, marketplace: str) -> tuple[dict[str, set[str]], dict[str, dict[str, dict[str, Any]]]]:
    books_by_source: dict[str, set[str]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT source.asin AS source_asin, book.asin AS book_asin
        FROM author_source_books source_book
        JOIN author_sources source ON source.id = source_book.source_id
        JOIN books book ON book.id = source_book.book_id
        WHERE source.marketplace = ? AND book.marketplace = ?
        """,
        (marketplace, marketplace),
    ):
        books_by_source[str(row["source_asin"])].add(str(row["book_asin"]))

    series_by_source: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in connection.execute(
        """
        SELECT source.asin AS source_asin, series.asin AS series_asin, series.name AS series_name
        FROM author_source_books source_book
        JOIN author_sources source ON source.id = source_book.source_id
        JOIN books book ON book.id = source_book.book_id
        JOIN book_series book_series ON book_series.book_id = book.id
        JOIN series series ON series.id = book_series.series_id
        WHERE source.marketplace = ? AND book.marketplace = ? AND series.marketplace = ?
        """,
        (marketplace, marketplace, marketplace),
    ):
        source_asin = str(row["source_asin"])
        series_asin = str(row["series_asin"] or "").strip()
        series_name = str(row["series_name"] or "").strip()
        key = f"asin:{series_asin}" if series_asin else f"name:{series_title_key(series_name)}"
        if key == "name:":
            continue
        series_by_source[source_asin].setdefault(
            key,
            {"asin": series_asin, "name": series_name},
        )
    return dict(books_by_source), {key: dict(value) for key, value in series_by_source.items()}


def _author_series(author: dict[str, Any], series_by_source: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    series: dict[str, dict[str, Any]] = {}
    for asin in source_asins(author):
        for key, item in series_by_source.get(asin, {}).items():
            series.setdefault(key, dict(item))
    return sorted(series.values(), key=lambda item: str(item.get("name") or "").casefold())


def _identity_members(
    author: dict[str, Any],
    books_by_source: dict[str, set[str]],
    series_by_source: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    source_records = author.get("source_identities") or []
    if not source_records:
        source_records = [{"asin": asin, "name": author.get("name")} for asin in source_asins(author)]
    seen: set[str] = set()
    members: list[dict[str, Any]] = []
    primary_asin = str(author.get("asin") or "")
    for source in source_records:
        if not isinstance(source, dict):
            continue
        asin = str(source.get("asin") or "").strip()
        if not asin or asin in seen:
            continue
        seen.add(asin)
        members.append({
            "asin": asin,
            "name": str(source.get("name") or author.get("name") or ""),
            "book_count": len(books_by_source.get(asin, set())),
            "series_count": len(series_by_source.get(asin, {})),
            "is_primary": asin == primary_asin,
        })
    return members


def _merged_payload(
    author: dict[str, Any],
    books_by_source: dict[str, set[str]],
    series_by_source: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    members = _identity_members(author, books_by_source, series_by_source)
    if len(members) < 2:
        return None
    books = set().union(*(books_by_source.get(member["asin"], set()) for member in members))
    series = set().union(*(set(series_by_source.get(member["asin"], {})) for member in members))
    return {
        "kind": "merged-author",
        "primary_asin": str(author.get("asin") or ""),
        "canonical": {
            "asin": str(author.get("asin") or ""),
            "name": str(author.get("name") or ""),
            "display_name": author.get("display_name"),
        },
        "members": members,
        "book_count": len(books),
        "series_count": len(series),
    }


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    members = []
    for member in candidate.get("members") or []:
        if not isinstance(member, dict):
            continue
        members.append({
            "asin": str(member.get("asin") or ""),
            "name": str(member.get("name") or ""),
            "book_count": int(member.get("book_count") or 0),
            "series_count": int(member.get("series_count") or 0),
            "source_asins": sorted(source_asins(member)),
        })
    return {
        "kind": "author-match",
        "asin": candidate.get("asin"),
        "marketplace": candidate.get("marketplace"),
        "primary_asin": candidate.get("primary_asin"),
        "members": members,
        "shared_series": [
            {
                "asin": item.get("asin"),
                "name": item.get("name") or item.get("title") or "",
            }
            for item in candidate.get("shared_series") or []
            if isinstance(item, dict)
        ],
        "shared_series_count": int(candidate.get("shared_series_count") or 0),
        "decision": candidate.get("decision"),
    }


def list_author_management(
    db,
    marketplace: str,
    min_shared_series: int = 1,
    *,
    include_merged: bool = True,
    include_candidates: bool = True,
) -> dict[str, Any]:
    """Return requested author-management categories without computing the others."""
    with db.identity_lock:
        consolidate_authors(db)
        authors = [
            author for author in list_authors(db, consolidate=False)
            if author.get("marketplace") == marketplace
        ]
        with db.transaction() as connection:
            decisions = read_decisions(connection) if include_candidates else {}
            books_by_source, series_by_source = _relation_data(connection, marketplace)
            add_search_evidence(connection, marketplace, books_by_source, series_by_source)

    enriched_authors = authors
    if include_candidates:
        enriched_authors = []
        for author in authors:
            enriched = dict(author)
            enriched["series"] = _author_series(enriched, series_by_source)
            enriched_authors.append(enriched)

    result: dict[str, Any] = {}
    if include_merged:
        merged = [
            item
            for author in authors
            if (item := _merged_payload(author, books_by_source, series_by_source)) is not None
        ]
        merged.sort(key=lambda item: str(item["canonical"].get("name") or "").casefold())
        result["merged"] = merged

    if include_candidates:
        candidates = author_merge_candidates(
            enriched_authors,
            books_by_source,
            decisions,
            include_separate=True,
            min_shared_series=min_shared_series,
        )
        managed_candidates = [_candidate_payload(candidate) for candidate in candidates]
        managed_candidates.sort(key=lambda item: (
            str((item.get("members") or [{}])[0].get("name") or "").casefold(),
            str(item.get("asin") or ""),
        ))
        result["candidates"] = managed_candidates
    return result
