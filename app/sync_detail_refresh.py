"""Refresh dependent modal caches when observed catalogue metadata changes."""

from __future__ import annotations
from datetime import datetime
from typing import Any
from .book_view import book_metadata, book_summary
from .normalization import person_name_key


class DetailRefreshMixin:
    @staticmethod
    def _detail_source_time(entry):
        """Compare observations against source age, not modal assembly time."""
        value = entry.payload.get("cache_source_fetched_at")
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else entry.fetched_at
        except (TypeError, ValueError):
            return entry.fetched_at

    @staticmethod
    def _product_relation_signature(items: Any, relation: str) -> tuple[tuple[str, ...], ...]:
        if not isinstance(items, list):
            return ()
        values: list[tuple[str, ...]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if relation == "authors":
                identity = person_name_key(item.get("name")) or str(item.get("asin") or "")
                values.append((identity, str(item.get("role") or "").casefold()))
            else:
                values.append(
                    (
                        str(item.get("asin") or ""),
                        person_name_key(item.get("name")),
                        str(item.get("sequence") or item.get("sequence_text") or ""),
                        str(item.get("sequence_number") or ""),
                    )
                )
        return tuple(sorted(values))

    @classmethod
    def _product_changed(cls, previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
        """Compare meaningful catalogue fields while ignoring sparse omissions."""

        if not previous:
            return True
        for key in (
            "title",
            "subtitle",
            "description",
            "release_date",
            "release_date_precision",
            "image_url",
            "audible_url",
            "publisher",
            "runtime_minutes",
            "language",
            "is_preorder",
            "is_available",
        ):
            incoming = current.get(key)
            if incoming in (None, "", []):
                continue
            existing = previous.get(key)
            if key in {"is_preorder", "is_available"}:
                if bool(existing) != bool(incoming):
                    return True
            elif str(existing or "") != str(incoming):
                return True
        for relation in ("authors", "series"):
            incoming = current.get(relation)
            if incoming:
                if cls._product_relation_signature(previous.get(relation), relation) != cls._product_relation_signature(incoming, relation):
                    return True
        return False

    @staticmethod
    def _cached_series_book(payload: dict[str, Any], asin: str) -> dict[str, Any] | None:
        for book in payload.get("books") or []:
            if isinstance(book, dict) and str(book.get("asin") or "") == str(asin):
                return book
        return None

    @staticmethod
    def _cached_author_book(payload: dict[str, Any], asin: str) -> dict[str, Any] | None:
        for group in payload.get("series") or []:
            if not isinstance(group, dict):
                continue
            for book in group.get("books") or []:
                if isinstance(book, dict) and str(book.get("asin") or "") == str(asin):
                    return book
        return None

    def _invalidate_author_details_for_product(
        self,
        product: dict[str, Any],
        previous: dict[str, Any] | None,
        observed_at: datetime | None,
    ) -> None:
        """Invalidate affected assembled author catalogues only when this book differs."""

        book_asin = str(product.get("asin") or "")
        author_asins = {
            str(author.get("asin") or "")
            for source in (previous or {}, product)
            for author in source.get("authors") or []
            if isinstance(author, dict) and author.get("asin")
        }
        if author_asins:
            with self.db.transaction() as connection:
                canonical = connection.execute(
                    "SELECT DISTINCT a.asin FROM author_sources s JOIN authors a ON a.id = s.author_id "
                    "WHERE s.marketplace = ? AND s.asin IN ({})".format(",".join("?" for _ in author_asins)),
                    (self.settings.region, *author_asins),
                ).fetchall()
            author_asins.update(row["asin"] for row in canonical)
        for author_asin in author_asins:
            entry = self._read_detail_entry("author", author_asin)
            if entry is None or (observed_at is not None and self._detail_source_time(entry) >= observed_at):
                continue
            cached_book = self._cached_author_book(entry.payload, book_asin)
            if cached_book is None or self._product_changed(cached_book, product):
                self._delete_detail_cache("author", author_asin)

    @staticmethod
    def _merged_book_detail(
        product: dict[str, Any],
        cached: dict[str, Any] | None,
    ) -> dict[str, Any]:
        book = book_summary(product)
        old_book = (cached or {}).get("book") if isinstance(cached, dict) else None
        if isinstance(old_book, dict):
            for key, value in old_book.items():
                if book.get(key) in (None, "", []):
                    book[key] = value
        metadata = book_metadata(product)
        old_metadata = (cached or {}).get("metadata") if isinstance(cached, dict) else None
        if isinstance(old_metadata, list):
            values = {item.get("label"): item for item in old_metadata if isinstance(item, dict) and item.get("label")}
            values.update({item.get("label"): item for item in metadata if item.get("label")})
            metadata = list(values.values())
        return {"kind": "book", "book": book, "metadata": metadata}

    def _refresh_detail_caches_for_product(
        self,
        product: dict[str, Any],
        previous: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        *,
        local_only: bool = False,
    ) -> None:
        """Update a cached book and invalidate affected author/series catalogues on change."""

        asin = str(product.get("asin") or "").strip()
        if not asin:
            return
        cached_book_entry = self._read_detail_entry("book", asin)
        cached_book = cached_book_entry.payload if cached_book_entry else None
        changed = self._product_changed(previous, product)
        if cached_book is not None and self._product_changed(cached_book.get("book"), product):
            changed = True
        if changed:
            self._invalidate_author_details_for_product(product, previous, observed_at)
        if observed_at is not None and cached_book_entry and self._detail_source_time(cached_book_entry) >= observed_at:
            return
        if changed and cached_book is not None:
            payload = self._merged_book_detail(product, cached_book)
            fetched_at = observed_at
            if local_only:
                fetched_at = cached_book_entry.fetched_at
                for key in ("cache_source_fetched_at", "cache_expires_at"):
                    if key in cached_book:
                        payload[key] = cached_book[key]
            self._write_detail_cache("book", asin, payload, fetched_at=fetched_at)

        series_values: dict[str, dict[str, Any]] = {}
        for source in (previous or {}, product):
            for series in source.get("series") or []:
                if isinstance(series, dict) and series.get("asin"):
                    series_values[str(series["asin"])] = series
        for series_asin in series_values:
            cached_series = self._read_detail_cache("series", series_asin)
            if cached_series is None:
                continue
            cached_series_book = self._cached_series_book(cached_series, asin)
            if cached_series_book is None or self._product_changed(cached_series_book, product):
                self._delete_detail_cache("series", series_asin)
