"""Category snapshot loading and manual catalogue discovery."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from .category_catalog import category_by_id
from .normalization import extract_products, is_english_language, normalize_product
from .sync_matching import product_release_date
from .time_zone import local_today
from .instrumentation import source_operation, record_pagination


CATEGORY_SNAPSHOT_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)


class CategorySyncMixin:
    def _category_snapshot_cache_key(self, category_id: str) -> str:
        return self.source_cache.make_key(
            "audible",
            "category-snapshot",
            {
                "category_id": str(category_id),
                "region": self.settings.region,
                "schema_version": CATEGORY_SNAPSHOT_SCHEMA_VERSION,
            },
        )

    def _read_category_snapshot(self, category_id: str) -> Any:
        cached = self.source_cache.read(self._category_snapshot_cache_key(category_id))
        return cached if cached and isinstance(cached.payload, dict) else None

    def _category_snapshot_view(
        self,
        snapshot: dict[str, Any],
        days: int,
        *,
        cached_at: datetime | None = None,
    ) -> dict[str, Any]:
        start = local_today(self.settings.time_zone)
        end = start + timedelta(days=days)
        products = [
            item
            for item in snapshot.get("products") or []
            if isinstance(item, dict)
            and (not self.settings.english_only or is_english_language(item.get("language")))
        ]
        result = {
            "category": dict(snapshot.get("category") or {}),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "items": self._category_public_items(products, start, end),
            "pages_fetched": int(snapshot.get("pages_fetched") or 0),
            "complete": bool(snapshot.get("complete")),
            "english_only": bool(self.settings.english_only),
        }
        if cached_at is not None:
            result["cached_at"] = cached_at.isoformat()
        return result

    def _cached_explicit_book_images(self, asins: set[str]) -> dict[str, str]:
        """Return cover URLs learned from an explicit local detail request.

        Category pages are broad catalogue observations.  Their out-of-window
        rows deliberately do not inherit cover art from the page payload: a
        cover is shown there only when a book was previously surfaced through
        a book, series, or author detail view and persisted locally.
        """

        requested = {str(asin).strip() for asin in asins if str(asin).strip()}
        if not requested:
            return {}
        images: dict[str, str] = {}
        placeholders = ", ".join("?" for _ in requested)
        with self.db.transaction() as connection:
            rows = connection.execute(
                f"""
                SELECT asin, image_url
                FROM books
                WHERE marketplace = ?
                  AND asin IN ({placeholders})
                  AND COALESCE(image_url, '') <> ''
                """,
                (self.settings.region, *sorted(requested)),
            ).fetchall()
        for row in rows:
            images[str(row["asin"])] = str(row["image_url"] or "")

        return images

    @staticmethod
    def _category_item_in_range(item: dict[str, Any], start: date, end: date) -> bool:
        release = product_release_date(item)
        return release is not None and start <= release <= end

    def _category_public_items(
        self,
        products: list[dict[str, Any]],
        start: date,
        end: date,
    ) -> list[dict[str, Any]]:
        """Build the cached category view, retaining lightweight distant rows."""

        covers = self._cached_explicit_book_images({str(item.get("asin") or "") for item in products})
        base_fields = (
            "asin",
            "marketplace",
            "title",
            "release_date",
            "release_date_precision",
            "language",
            "audible_url",
            "is_preorder",
            "is_available",
        )
        public_items: list[dict[str, Any]] = []
        for product in products:
            asin = str(product.get("asin") or "")
            if not asin or not product.get("release_date"):
                continue
            in_range = self._category_item_in_range(product, start, end)
            if in_range:
                public_item = dict(product)
                # Preserve an explicitly cached cover if the broad category
                # payload happens to omit one.
                public_item["image_url"] = public_item.get("image_url") or covers.get(asin, "")
            else:
                # Out-of-window rows are intentionally base-only.  In
                # particular, do not expose a category-page cover or rich
                # relations unless a detail request has already cached one.
                public_item = {
                    key: product.get(key)
                    for key in base_fields
                    if product.get(key) not in (None, "")
                }
                public_item["image_url"] = covers.get(asin, "")
            public_item["category_in_range"] = in_range
            public_item.pop("source_payload", None)
            public_items.append(public_item)
        return public_items

    def cached_category_upcoming(self, category_id: str, days: int) -> dict[str, Any] | None:
        """Return the last completed category check without contacting Audible."""

        category = category_by_id(str(category_id))
        if not category:
            raise ValueError("Unknown Audible category")
        if days < 1 or days > 3650:
            raise ValueError("days must be between 1 and 3650")
        cached = self._read_category_snapshot(str(category_id))
        if not cached or not isinstance(cached.payload, dict):
            return None
        return self._category_snapshot_view(cached.payload, days, cached_at=cached.fetched_at)

    @source_operation("category")
    async def category_upcoming(self, category_id: str, days: int, *, force: bool = False) -> dict[str, Any]:
        """Load a manually requested category window without touching user lists.

        Audible sorts this endpoint with the furthest release dates first. We
        therefore page until the feed crosses today, normalize every dated
        product into one category snapshot, and derive the requested language
        and day window locally. Only ``force`` refreshes that snapshot.
        """

        category = category_by_id(str(category_id))
        if not category:
            raise ValueError("Unknown Audible category")
        if days < 1 or days > 3650:
            raise ValueError("days must be between 1 and 3650")
        if not force:
            cached = self.cached_category_upcoming(str(category_id), days)
            if cached is not None:
                return cached

        today = local_today(self.settings.time_zone)
        end_date = today + timedelta(days=days)
        page_size = 50
        max_pages = 100
        recent_launch_pages = 20
        pages_fetched = 0
        complete = False

        def page_fingerprint(raw_products: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
            return tuple(
                (
                    str(product.get("asin") or ""),
                    str(product.get("title") or ""),
                    str(product.get("releaseDate") or product.get("release_date") or ""),
                )
                for product in raw_products
            )

        all_products: dict[str, dict[str, Any]] = {}

        def collect_products(raw_products: list[dict[str, Any]]) -> list[date]:
            page_products = [
                normalize_product(raw_product, self.settings.region)
                for raw_product in raw_products
                if isinstance(raw_product, dict)
            ]
            page_dates: list[date] = []
            for product in page_products:
                release_value = product.get("release_date")
                try:
                    release = date.fromisoformat(str(release_value)[:10])
                except (TypeError, ValueError):
                    continue
                page_dates.append(release)
                snapshot_product = dict(product)
                snapshot_product.pop("source_payload", None)
                all_products[product["asin"]] = snapshot_product
                if self.settings.english_only and not is_english_language(product.get("language")):
                    continue
                if today <= release <= end_date:
                    # Category refreshes are another independent observation
                    # of catalogue data. If it differs from a cached modal
                    # payload, update the book cache and invalidate affected
                    # series data. Distant rows remain base-only until the
                    # user explicitly opens one.
                    self._refresh_detail_caches_for_product(product)
            return page_dates

        async def fetch_page(page: int, sort_by: str) -> list[dict[str, Any]]:
            nonlocal pages_fetched
            payload = await self.audible.get_category_products_page(
                str(category_id), page,
                force=force,
                sort_by=sort_by,
            )
            pages_fetched += 1
            return extract_products(payload)

        seen_release_pages: set[tuple[tuple[str, str, str], ...]] = set()
        repeated_release_page = False
        for page in range(max_pages):
            raw_products = await fetch_page(page, "-ReleaseDate")
            if not raw_products:
                record_pagination(self, "empty")
                complete = True
                break
            fingerprint = page_fingerprint(raw_products)
            if fingerprint in seen_release_pages:
                record_pagination(self, "repeat")
                repeated_release_page = True
                break
            seen_release_pages.add(fingerprint)
            page_dates = collect_products(raw_products)

            # With -ReleaseDate, a page containing a pre-today date means
            # subsequent pages cannot contribute to the upcoming window.
            if page_dates and min(page_dates) < today:
                record_pagination(self, "past")
                complete = True
                break
            if len(raw_products) < page_size:
                record_pagination(self, "short")
                complete = True
                break

        if len(seen_release_pages) >= max_pages and not complete:
            record_pagination(self, "limit")

        # Some large subcategories return the same final page indefinitely
        # instead of an empty page. A recent-launch feed is a useful second
        # view of the catalogue: it avoids the far-future placeholder wall
        # while still surfacing products added near the requested window.
        if not complete and (repeated_release_page or len(seen_release_pages) >= max_pages):
            seen_launch_pages: set[tuple[tuple[str, str, str], ...]] = set()
            for page in range(recent_launch_pages):
                raw_products = await fetch_page(page, "ProductSiteLaunchDate")
                if not raw_products:
                    record_pagination(self, "empty")
                    break
                fingerprint = page_fingerprint(raw_products)
                if fingerprint in seen_launch_pages:
                    record_pagination(self, "repeat")
                    break
                seen_launch_pages.add(fingerprint)
                collect_products(raw_products)
                if len(raw_products) < page_size:
                    record_pagination(self, "short")
                    break
            logger.warning(
                "category overview for %s reached a pagination boundary; used recent-launch fallback",
                category["name"],
            )

        products = sorted(
            all_products.values(),
            key=lambda item: (item.get("release_date") or "9999-12-31", (item.get("title") or "").casefold()),
        )
        category_label = category["name"]
        if category.get("parent_name"):
            category_label = f"{category['parent_name']} / {category['name']}"
        snapshot = {
            "schema_version": CATEGORY_SNAPSHOT_SCHEMA_VERSION,
            "region": self.settings.region,
            "category": {
                "id": category["id"],
                "name": category["name"],
                "parent_name": category.get("parent_name"),
                "label": category_label,
            },
            "products": products,
            "pages_fetched": pages_fetched,
            "complete": complete,
        }
        self.source_cache.write(
            self._category_snapshot_cache_key(str(category_id)),
            "audible",
            "category-snapshot",
            snapshot,
        )
        return self._category_snapshot_view(snapshot, days)
