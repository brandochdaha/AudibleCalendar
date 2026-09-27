"""Per-target catalogue discovery and persistence."""

from __future__ import annotations

from .author_discovery import resolve_catalogue_author
from .author_sources import author_search_names

import asyncio
from starlette.concurrency import run_in_threadpool
from datetime import datetime, timedelta, timezone
from typing import Any

from .catalogue_snapshot import CatalogueSnapshot
from .normalization import (
    extract_products,
    infer_missing_series,
    infer_series_item_from_title,
    is_followable_author,
    normalize_audnexus_book,
    normalize_products,
    series_author_asins,
    series_identity_matches,
)
from .repository import (
    list_authors,
    list_followed_books,
    list_followed_series,
    list_selected_author_book_ids,
    list_selected_author_series,
    list_series,
    update_sync_state,
    upsert_author,
    upsert_book,
    upsert_series,
)
from .source_precedence import enrich_missing_fields
from .sources.audible import SourceError
from .sync_matching import author_candidate_matches, is_past_release, product_release_date
from .sync_runtime import mark_sync_completed, mark_sync_started
from .time_zone import local_today
from .instrumentation import record_pagination


DISCOVERY_LOOKBACK_DAYS = 30


class CatalogueTargetsMixin:
    async def _sync_author(
        self,
        author: dict[str, Any],
        snapshot: CatalogueSnapshot,
        *,
        force: bool = False,
    ) -> int:
        (await run_in_threadpool(update_sync_state, self.db, "author", author["id"], started=True, error=None))
        matched_products: dict[str, dict[str, Any]] = {}
        catalogue_changed = False
        discovery_start = local_today(self.settings.time_zone) - timedelta(days=DISCOVERY_LOOKBACK_DAYS)
        source_error: SourceError | None = None
        for search_name in author_search_names(author):
            try:
                for page in range(self.settings.sync_max_pages):
                    self.progress.set_step("catalog search", page + 1)
                    payload = await self._cached_sync_call(
                        ("search_products_page", search_name, "author", page),
                        lambda: self.audible.search_products_page(
                            search_name, "author", page,
                            # Discovery must see late listings even when browsing
                            # recently populated the response cache.
                            force=True,
                            cache_ttl_seconds=self.settings.cache_author_hours * 60 * 60,
                        ),
                    )
                    products = normalize_products(payload, self.settings.region, english_only=self.settings.english_only)
                    if not extract_products(payload):
                        record_pagination(self, "empty")
                        break
                    author = (await run_in_threadpool(resolve_catalogue_author, self.db, author, products, self.settings.region))
                    page_dates = [product_release_date(product) for product in products]
                    for product in products:
                        release = product_release_date(product)
                        if release is not None and release < discovery_start:
                            continue
                        if any(author_candidate_matches(item, author) for item in product["authors"]):
                            matched_products[product["asin"]] = product
                    # Results are newest-first, so older pages cannot add a
                    # recent or upcoming title after crossing the catch-up window.
                    if page_dates and all(release is not None for release in page_dates) and min(page_dates) < discovery_start:
                        record_pagination(self, "past")
                        break
                    if len(extract_products(payload)) < self.settings.sync_page_size:
                        record_pagination(self, "short")
                        break
                else:
                    record_pagination(self, "limit")
                    source_error = source_error or SourceError(f"Catalog results may be incomplete: reached the {page + 1}-page discovery limit")
            except SourceError as exc:
                source_error = source_error or exc
        enriched_products = [
            await self._enrich_book_from_audnexus(product, force=force)
            for product in matched_products.values()
        ]
        infer_missing_series(enriched_products, author_identities=[author], known_products=snapshot.books)
        for product in enriched_products:
            previous = snapshot.get(product.get("asin"))
            catalogue_changed = catalogue_changed or self._product_changed(previous, product)
            book_id = (await run_in_threadpool(upsert_book, self.db, product, self.settings.region))
            (await run_in_threadpool(self._refresh_detail_caches_for_product, product, previous, self._sync_product_observed_at(product)))
            snapshot.remember(product, book_id=book_id)
        if catalogue_changed:
            # The searched contributor can be name-only on individual product
            # rows, so also invalidate the canonical target explicitly.
            (await run_in_threadpool(self._delete_detail_cache, "author", str(author.get("asin") or "")))
        seen = len(matched_products)
        if source_error:
            raise source_error
        (await run_in_threadpool(update_sync_state, self.db, "author", author["id"], success=True, items_seen=seen))
        return seen

    async def _sync_series(
        self,
        series: dict[str, Any],
        snapshot: CatalogueSnapshot,
        *,
        force: bool = False,
    ) -> int:
        (await run_in_threadpool(update_sync_state, self.db, "series", series["id"], started=True, error=None))
        # Generated identities cannot be used with /sims, but their names are
        # still valid bounded-search targets and title inference can restore
        # the missing relation on newly discovered products.
        has_stable_asin = not str(series["asin"]).startswith("name:")
        seeds = snapshot.for_series(series["asin"])
        seen_asins: set[str] = set()
        source_error: SourceError | None = None
        seen = 0
        today = local_today(self.settings.time_zone)
        discovery_start = today - timedelta(days=DISCOVERY_LOOKBACK_DAYS)
        with self.db.transaction() as connection:
            target_author_asins = {
                str(row["asin"])
                for row in connection.execute(
                    """
                    SELECT a.asin
                    FROM book_series bs
                    JOIN book_authors ba ON ba.book_id = bs.book_id
                    JOIN authors a ON a.id = ba.author_id
                    WHERE bs.series_id = ?
                    UNION
                    SELECT source.asin
                    FROM book_series bs
                    JOIN book_authors ba ON ba.book_id = bs.book_id
                    JOIN author_sources source ON source.author_id = ba.author_id
                    WHERE bs.series_id = ?
                    UNION
                    SELECT a.asin
                    FROM author_series_preferences asp
                    JOIN authors a ON a.id = asp.author_id
                    WHERE asp.series_id = ? AND asp.is_selected = 1
                    UNION
                    SELECT source.asin
                    FROM author_series_preferences asp
                    JOIN author_sources source ON source.author_id = asp.author_id
                    WHERE asp.series_id = ? AND asp.is_selected = 1
                    """,
                    (series["id"], series["id"], series["id"], series["id"]),
                ).fetchall()
                if row["asin"]
            }
        target = {"asin": str(series["asin"]), "name": series.get("name") or ""}
        author_identities = [
            author for author in await run_in_threadpool(list_authors, self.db, consolidate=False)
            if author.get("marketplace") == self.settings.region
        ]

        async def add_products(payload: Any) -> None:
            nonlocal seen
            for product in normalize_products(payload, self.settings.region, english_only=self.settings.english_only):
                release = product_release_date(product)
                if release is not None and release < discovery_start:
                    continue
                matches_series = any(series_identity_matches(item, target) for item in product["series"])
                if not matches_series and not product.get("series"):
                    inferred = infer_series_item_from_title(product.get("title"), series)
                    if inferred and series_author_asins(product.get("authors"), author_identities) & target_author_asins:
                        product["series"] = [*product.get("series", []), inferred]
                        matches_series = True
                if not matches_series:
                    continue
                product = await self._enrich_book_from_audnexus(product, force=force)
                previous = snapshot.get(product.get("asin"))
                book_id = (await run_in_threadpool(upsert_book, self.db, product, self.settings.region))
                (await run_in_threadpool(self._refresh_detail_caches_for_product, product, previous, self._sync_product_observed_at(product)))
                snapshot.remember(product, book_id=book_id)
                (await run_in_threadpool(self._follow_series_authors, product.get("authors", []), series["id"], self.settings.region))
                if product["asin"] not in seen_asins:
                    seen_asins.add(product["asin"])
                    seen += 1

        # One seed is enough to discover related products; the bounded search
        # below is the completeness path and avoids multiplying /sims calls.
        if has_stable_asin:
            for seed in seeds[:1]:
                try:
                    self.progress.set_step("similar books")
                    await add_products(await self._cached_sync_call(
                        ("get_similar", seed["asin"]),
                        lambda: self.audible.get_similar(seed["asin"], force=force),
                    ))
                except SourceError as exc:
                    source_error = source_error or exc

        # Similar-product results are not guaranteed to include every volume.
        # Supplement them with a bounded catalogue search, just as the detail
        # view does, so a series does not remain stuck on its initial seed.
        try:
            for page in range(min(self.settings.sync_max_pages, 3)):
                self.progress.set_step("catalog search", page + 1)
                payload = await self._cached_sync_call(
                    ("search_products_page", series["name"], "series", page),
                    lambda: self.audible.search_products_page(
                        series["name"], "series", page,
                        force=True,
                        cache_ttl_seconds=self.settings.cache_series_hours * 60 * 60,
                    ),
                )
                products = normalize_products(payload, self.settings.region, english_only=self.settings.english_only)
                if not extract_products(payload):
                    record_pagination(self, "empty")
                    break
                await add_products(payload)
                page_dates = [product_release_date(product) for product in products]
                if page_dates and all(release is not None for release in page_dates) and min(page_dates) < discovery_start:
                    record_pagination(self, "past")
                    break
                if len(extract_products(payload)) < self.settings.sync_page_size:
                    record_pagination(self, "short")
                    break
            else:
                record_pagination(self, "limit")
                source_error = source_error or SourceError(f"Catalog results may be incomplete: reached the {page + 1}-page discovery limit")
        except SourceError as exc:
            source_error = source_error or exc

        # Search and similar-product results normally contain the known seed.
        # Only fall back to a targeted detail request when they omitted an
        # upcoming/undated seed; never spend that extra request on old books.
        seed = seeds[0] if seeds else None
        if (
            seed
            and seed.get("asin") not in seen_asins
            and not is_past_release(seed, today)
        ):
            try:
                self.progress.set_step("seed details")
                await add_products(await self._cached_sync_call(
                    ("get_product", seed["asin"]),
                    lambda: self.audible.get_product(seed["asin"], force=force),
                ))
            except SourceError as exc:
                source_error = source_error or exc

        if source_error:
            raise source_error
        (await run_in_threadpool(update_sync_state, self.db, "series", series["id"], success=True, items_seen=seen))
        return seen

    async def _sync_book(
        self,
        book: dict[str, Any],
        snapshot: CatalogueSnapshot,
        *,
        force: bool = False,
    ) -> None:
        (await run_in_threadpool(update_sync_state, self.db, "book", book["id"], started=True, error=None))
        previous = snapshot.get(book.get("asin"))
        self.progress.set_step("book details")
        try:
            payload = await self._cached_sync_call(
                ("get_product", book["asin"]),
                lambda: self.audible.get_product(book["asin"], force=force),
            )
            products = normalize_products(payload, self.settings.region, english_only=self.settings.english_only)
        except SourceError as audible_error:
            if not self.settings.audnexus_enabled:
                raise
            try:
                nexus_payload = await self._cached_sync_call(
                    ("audnexus_book", book["asin"]),
                    lambda: self.audnexus.get_book(book["asin"], self.settings.region, force=force),
                )
            except SourceError:
                raise audible_error
            products = [enrich_missing_fields(book, normalize_audnexus_book(nexus_payload, self.settings.region))] if nexus_payload else []
        if products:
            product = await self._enrich_book_from_audnexus(products[0], force=force)
            book_id = (await run_in_threadpool(upsert_book, self.db, product, self.settings.region, tracked=True))
            for series in product.get("series", []):
                if not series.get("asin") or not series.get("name"):
                    continue
                series_id = (await run_in_threadpool(upsert_series, self.db, series, self.settings.region, tracked=True))
                (await run_in_threadpool(self._follow_series_authors, product.get("authors", []), series_id, self.settings.region))
            for author in product.get("authors", []):
                if not is_followable_author(author):
                    continue
                if author.get("asin") and author.get("name"):
                    (await run_in_threadpool(upsert_author,
                        self.db,
                        {**author, "follow_all": False},
                        self.settings.region,
                        tracked=True,
                    ))
            (await run_in_threadpool(self._refresh_detail_caches_for_product, product, previous, self._sync_product_observed_at(product)))
            snapshot.remember(product, book_id=book_id, tracked=True)
        (await run_in_threadpool(update_sync_state, self.db, "book", book["id"], success=True, items_seen=1))
