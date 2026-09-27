"""Series and individual-book details assembled from the local and public catalogue."""

from __future__ import annotations

from typing import Any

from .instrumentation import record_pagination
from .book_view import book_metadata, book_summary
from .config import Settings
from .db import Database
from .author_search import group_search_author_records
from .author_identity import canonicalize_author_records, author_identities_can_merge
from .normalization import (
    extract_products,
    infer_series_item_from_title,
    normalize_audnexus_book,
    normalize_products,
    person_name_key,
    series_author_asins,
    series_identity_matches,
    _domain_for_marketplace,
)
from .repository import get_book, list_author_series_preferences, list_authors, list_books, list_series
from .source_precedence import enrich_missing_fields
from .sources.audible import SourceError


class CatalogueDetailsBuilder:
    def __init__(self, db: Database, settings: Settings, audible: Any, audnexus: Any):
        self.db = db
        self.settings = settings
        self.audible = audible
        self.audnexus = audnexus

    @staticmethod
    def _series_matches(product: dict[str, Any], asin: str, name: str) -> bool:
        target = {"asin": asin, "name": name}
        return any(
            series_identity_matches(item, target)
            for item in product.get("series", [])
        )

    async def book(self, asin: str, name: str = "") -> dict[str, Any]:
        asin = asin.strip()
        if not asin:
            raise ValueError("Book ASIN is required")
        product: dict[str, Any] | None = None
        source_error: str | None = None
        try:
            products = normalize_products(
                await self.audible.get_product(asin),
                self.settings.region,
                english_only=False,
            )
            product = products[0] if products else None
        except SourceError as exc:
            source_error = str(exc)

        # Some public product responses contain only the ASIN. A title search
        # usually returns the richer catalogue shape used by the rest of the
        # app, so use it when the detail response is only a shell.
        if product and product.get("title") == "Untitled audiobook" and name:
            try:
                search_products = normalize_products(
                    await self.audible.search_products(name, "book"),
                    self.settings.region,
                    english_only=False,
                )
                product = next((item for item in search_products if item.get("asin") == asin), product)
            except SourceError as exc:
                source_error = source_error or str(exc)

        if product and self.settings.audnexus_enabled:
            try:
                enriched = await self.audnexus.get_book(asin, self.settings.region)
                if enriched:
                    enrichment = normalize_audnexus_book(enriched, self.settings.region)
                    product = enrich_missing_fields(product, enrichment)
            except SourceError:
                pass

        local = get_book(self.db, asin, self.settings.region)
        if local:
            product = enrich_missing_fields(product, book_summary(local)) if product else book_summary(local)
        if product is None:
            product = {
                "asin": asin,
                "title": name or "Untitled audiobook",
                "authors": [],
                "series": [],
                "audible_url": f"https://www.audible.{self._domain(self.settings.region)}/pd/{asin}",
            }

        result = {
            "kind": "book",
            "book": book_summary(product),
            "metadata": book_metadata(product),
        }
        if source_error:
            result["error"] = source_error
        return result

    async def series(self, asin: str, name: str = "", seed_asin: str = "") -> dict[str, Any]:
        asin = asin.strip()
        name = name.strip()
        if not asin:
            raise ValueError("Series ASIN is required")

        series_name = name or asin
        seed_asins: list[str] = [seed_asin] if seed_asin else []
        local_series = next(
            (item for item in list_series(self.db) if item.get("asin") == asin),
            None,
        )
        if local_series:
            series_name = local_series.get("name") or series_name
        for book in list_books(self.db):
            if any(series_identity_matches(item, {"asin": asin, "name": series_name}) for item in book.get("series", [])):
                if book.get("asin") and book["asin"] not in seed_asins:
                    seed_asins.append(book["asin"])
        # A single seed is sufficient for related-product discovery. The
        # bounded series search below supplies completeness without making
        # one request per locally known volume.
        seed_asins = seed_asins[:1]

        products: dict[str, dict[str, Any]] = {}
        possible_products: dict[str, dict[str, Any]] = {}
        local_authors = [author for author in list_authors(self.db)
                         if author["marketplace"] == self.settings.region]
        target_author_asins: set[str] = set()
        source_error: str | None = None

        async def add_products(payload: Any) -> None:
            for product in normalize_products(payload, self.settings.region, english_only=self.settings.english_only):
                if self._series_matches(product, asin, series_name):
                    products[product["asin"]] = product
                    target_author_asins.update(series_author_asins(product.get("authors"), local_authors))
                elif not product.get("series"):
                    possible_products[product["asin"]] = product

        for seed in seed_asins:
            try:
                similar_payload = await self.audible.get_similar(seed)
                await add_products(similar_payload)
            except SourceError as exc:
                source_error = str(exc)

        # Related-product responses are useful for discovering a series, but
        # they are not guaranteed to contain every volume (and some Audible
        # responses use a different response key). Always supplement them with
        # a bounded series search so a single seed never becomes the complete
        # result by accident.
        try:
            for page in range(min(self.settings.sync_max_pages, 3)):
                payload = await self.audible.search_products_page(
                    series_name, "series", page, cache_ttl_seconds=self.settings.cache_series_hours * 3600,
                )
                page_products = normalize_products(payload, self.settings.region, english_only=self.settings.english_only)
                if not extract_products(payload):
                    record_pagination(self, "empty")
                    break
                await add_products(payload)
                if len(extract_products(payload)) < self.settings.sync_page_size:
                    record_pagination(self, "short")
                    break
            else:
                record_pagination(self, "limit")
                source_error = source_error or f"Catalog results may be incomplete: reached the {page + 1}-page discovery limit"
        except SourceError as exc:
            source_error = source_error or str(exc)

        # The related and search responses request the same product groups as
        # the seed endpoint. Fetch the seed separately when discovery did not
        # provide its title, date, and author relationships.
        for seed in seed_asins:
            observed = products.get(seed)
            if (observed and observed.get("title") != "Untitled audiobook"
                    and observed.get("release_date") and observed.get("authors")):
                continue
            try:
                await add_products(await self.audible.get_product(seed))
            except SourceError as exc:
                source_error = source_error or str(exc)

        target = {"asin": asin, "name": series_name}
        for product in possible_products.values():
            inferred = infer_series_item_from_title(product.get("title"), target)
            if inferred and series_author_asins(product.get("authors"), local_authors) & target_author_asins:
                product["series"] = [inferred]
                products[product["asin"]] = product

        books = []
        for product in products.values():
            series_item = next(
                (item for item in product.get("series", []) if series_identity_matches(item, target)),
                {},
            )
            book = book_summary(product)
            book["series_sequence"] = series_item.get("sequence")
            book["series_sequence_number"] = series_item.get("sequence_number")
            books.append(book)
        books.sort(
            key=lambda book: (
                book.get("series_sequence_number") is None,
                book.get("series_sequence_number") if book.get("series_sequence_number") is not None else 0,
                book.get("release_date") or "",
                (book.get("title") or "").casefold(),
            )
        )

        authors: dict[str, dict[str, Any]] = {}
        author_books: dict[str, set[str]] = {}
        for product in products.values():
            for person in product.get("authors", []):
                role = str(person.get("role") or "").casefold()
                if role and any(term in role for term in ("narrat", "reader", "performer")):
                    continue
                person_name = (person.get("name") or "").strip()
                if not person_name:
                    continue
                name_key = person_name_key(person_name) or person_name.casefold()
                person_asin = str(person.get("asin") or f"name:{name_key}")
                # A stable Audible author ASIN is the identity key. The
                # display name can vary between products (for example,
                # "Brian J. Nordon" vs "Brian Nordon") without creating a
                # second author row. Name-derived identities remain keyed by
                # their normalized name until stronger evidence is available.
                person_key = (
                    f"asin:{person_asin.casefold()}"
                    if not person_asin.startswith("name:")
                    else f"name:{name_key}"
                )
                author_books.setdefault(person_key, set()).add(str(product.get("asin") or ""))
                candidate = {
                    "asin": person_asin,
                    "name": person_name,
                    "image_url": person.get("image_url") or person.get("image") or "",
                }
                existing = authors.get(person_key)
                if not existing:
                    authors[person_key] = candidate
                    continue
                # Audible sometimes omits an author's ASIN on only some
                # books. Prefer the stable identity over the generated name:
                # identity, while retaining any image metadata we have.
                if existing["asin"].startswith("name:") and not person_asin.startswith("name:"):
                    existing["asin"] = person_asin
                if not existing.get("image_url") and candidate.get("image_url"):
                    existing["image_url"] = candidate["image_url"]

        author_books_by_asin = {
            authors[key]["asin"]: author_books.get(key, set())
            for key in authors
        }
        merged_authors = canonicalize_author_records(list(authors.values()), local_authors, author_books_by_asin)
        merged_authors = group_search_author_records(merged_authors, author_books_by_asin)
        preferences_by_author = {
            author["id"]: list_author_series_preferences(self.db, author["id"])
            for author in local_authors
        }

        def matching_local_authors(author: dict[str, Any]) -> list[dict[str, Any]]:
            return [local for local in local_authors if author_identities_can_merge(author, local)]

        for author in merged_authors:
            matches = matching_local_authors(author)
            author["is_followed"] = any(local.get("is_tracked") for local in matches)
            author["is_following_series"] = any(
                local.get("is_tracked")
                and (
                    (
                        local.get("follow_all")
                        and not any(
                            not preference.get("is_selected")
                            and preference.get("series_asin") == asin
                            for preference in preferences_by_author.get(local["id"], [])
                        )
                    )
                    or any(
                        preference.get("is_selected") and preference.get("series_asin") == asin
                        for preference in preferences_by_author.get(local["id"], [])
                    )
                )
                for local in matches
            )

        local_series = next((item for item in list_series(self.db) if item.get("asin") == asin), None)
        followed_via_author = False
        # A broad author follow includes every series, even when the series
        # itself is not marked directly tracked. A selected-series preference
        # narrows that relationship back to the explicit series only.
        for author in merged_authors:
            for local in matching_local_authors(author):
                if not local.get("is_tracked"):
                    continue
                preferences = preferences_by_author.get(local["id"], [])
                if local.get("follow_all") and not any(
                    not preference.get("is_selected")
                    and (
                        preference.get("series_id") == (local_series or {}).get("id")
                        or preference.get("series_asin") == asin
                    )
                    for preference in preferences
                ):
                    followed_via_author = True
                    break
                if any(
                    preference.get("is_selected")
                    and (
                        preference.get("series_id") == (local_series or {}).get("id")
                        or preference.get("series_asin") == asin
                    )
                    for preference in preferences
                ):
                    followed_via_author = True
                    break
            if followed_via_author:
                break

        result = {
            "kind": "series",
            "series": {
                "asin": asin,
                "name": series_name,
                "is_followed": bool((local_series and local_series.get("is_tracked")) or followed_via_author),
                "is_followed_directly": bool(local_series and local_series.get("is_tracked")),
                "is_followed_via_author": followed_via_author,
            },
            "authors": sorted(merged_authors, key=lambda author: (author.get("display_name") or author["name"]).casefold()),
            "books": books,
            "book_count": len(books),
        }
        if source_error:
            result["error"] = source_error
        return result

    @staticmethod
    def _domain(region: str) -> str:
        return _domain_for_marketplace(region)
