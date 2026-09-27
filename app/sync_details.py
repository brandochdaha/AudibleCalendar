"""Persistent detail-cache orchestration for catalogue modals."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .author_sources import source_asins
from .author_details import AuthorDetailsBuilder
from .author_identity import author_identities_can_merge, canonicalize_author_records
from .author_search import group_search_author_records
from .catalogue_details import CatalogueDetailsBuilder
from .normalization import series_identity_matches
from .sources.request_cache import SourceError, capture_source_reads
from .instrumentation import source_operation
from .time_zone import local_today
from .repository import (
    list_author_book_preferences,
    list_author_series_preferences,
    list_authors,
    list_series,
    upsert_book,
)


DETAIL_CACHE_SCHEMA_VERSION = 5


from .sync_detail_refresh import DetailRefreshMixin


class DetailSyncMixin(DetailRefreshMixin):
    def _detail_cache_key(self, kind: str, asin: str) -> str:
        return self.source_cache.make_key(
            "audible",
            "detail",
            {
                "kind": kind,
                "asin": str(asin),
                "region": self.settings.region,
                "english_only": bool(self.settings.english_only),
                "audnexus_enabled": bool(self.settings.audnexus_enabled),
                "schema_version": DETAIL_CACHE_SCHEMA_VERSION,
            },
        )

    def _read_detail_cache(self, kind: str, asin: str) -> dict[str, Any] | None:
        entry = self._read_detail_entry(kind, asin)
        if not entry:
            return None
        return entry.payload if isinstance(entry.payload, dict) else None

    def _read_detail_entry(self, kind: str, asin: str) -> Any:
        entry = self.source_cache.read(self._detail_cache_key(kind, asin))
        if not entry or not isinstance(entry.payload, dict):
            return None
        return entry

    def _write_detail_cache(self, kind: str, asin: str, payload: dict[str, Any], *, fetched_at=None) -> None:
        self.source_cache.write(
            self._detail_cache_key(kind, asin),
            "audible",
            "detail",
            payload,
            fetched_at=fetched_at,
        )

    def _delete_detail_cache(self, kind: str, asin: str) -> None:
        if asin:
            self.source_cache.delete(self._detail_cache_key(kind, asin))

    def _detail_max_age(self, kind: str, asin: str) -> float:
        if kind in ("author", "series"):
            return getattr(self.settings, f"cache_{kind}_hours") * 3600
        return self.source_cache.product_max_age_seconds(
            asin, self.settings.region, upcoming_hours=self.settings.cache_upcoming_hours,
            far_future_hours=self.settings.cache_far_future_hours, released_days=self.settings.cache_released_days,
            today=local_today(self.settings.time_zone),
        )

    async def _load_persisted_detail(
        self,
        kind: str,
        asin: str,
        loader: Any,
        should_persist: Any = None,
        *,
        refresh: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Return a detail payload and whether it came from persistent cache."""

        key = self._detail_cache_key(kind, asin)
        requested_at = datetime.now(timezone.utc)
        max_age = self._detail_max_age(kind, asin)

        def reusable(entry):
            expires_at = entry.payload.get("cache_expires_at") if entry else None
            try:
                source_expired = expires_at and datetime.fromisoformat(expires_at) <= requested_at
            except (TypeError, ValueError):
                return False
            return (entry and not entry.payload.get("error")
                    and self.source_cache.is_fresh(entry, max_age)
                    and not source_expired
                    and (not refresh or entry.fetched_at >= requested_at))

        cached = self._read_detail_entry(kind, asin)
        if reusable(cached):
            return cached.payload, True
        lock = self._detail_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._read_detail_entry(kind, asin)
            if reusable(cached):
                return cached.payload, True
            with capture_source_reads(force=refresh, started_at=requested_at) as reads:
                try:
                    result = await loader()
                except SourceError as exc:
                    if cached is None:
                        raise
                    result = {**cached.payload, "error": str(exc)}
            if reads.stale or result.get("error"):
                warning = result.get("error") or "Audible could not be refreshed; showing cached details."
                if cached is not None:
                    return {**cached.payload, "error": warning}, True
                result = {**result, "error": warning}
                return result, reads.fetched_at is not None
            if not result.get("error") and (should_persist is None or should_persist(result)):
                # Sources have different lifetimes (e.g. a released seed book
                # versus a series search). Keep the earliest expiry, rather
                # than treating the oldest valid response as already stale.
                if reads.expires_at:
                    result = {**result, "cache_expires_at": reads.expires_at.isoformat()}
                if reads.fetched_at:
                    result = {**result, "cache_source_fetched_at": reads.fetched_at.isoformat()}
                self._write_detail_cache(kind, asin, result)
            return result, False

    _local_author_matches = staticmethod(author_identities_can_merge)
    _canonicalize_author_records = staticmethod(canonicalize_author_records)

    def _with_series_follow_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Overlay local follow state on a persisted catalogue response."""

        result = dict(payload)
        series = dict(result.get("series") or {})
        series_asin = str(series.get("asin") or "")
        local_series = next(
            (
                item
                for item in list_series(self.db)
                if series_identity_matches(item, series)
            ),
            None,
        )
        books_by_author: dict[str, set[str]] = {}
        for book in result.get("books") or []:
            if not isinstance(book, dict):
                continue
            book_asin = str(book.get("asin") or "")
            for raw_author in book.get("authors") or []:
                if isinstance(raw_author, dict) and raw_author.get("asin"):
                    books_by_author.setdefault(str(raw_author["asin"]), set()).add(book_asin)
        authors = []
        via_author = False
        local_authors = [author for author in list_authors(self.db) if author["marketplace"] == self.settings.region]
        payload_authors = self._canonicalize_author_records(
            result.get("authors") or [],
            local_authors,
            books_by_author,
        )
        payload_authors = group_search_author_records(payload_authors, books_by_author)
        for raw_author in payload_authors:
            author = dict(raw_author)
            matches = [local for local in local_authors if self._local_author_matches(author, local)]
            author_followed = False
            following_series = False
            for local in matches:
                if not local.get("is_tracked"):
                    continue
                author_followed = True
                preferences = list_author_series_preferences(self.db, local["id"])
                excluded = any(
                    not preference.get("is_selected")
                    and (
                        (series_asin and preference.get("series_asin") == series_asin)
                        or (local_series and preference.get("series_id") == local_series.get("id"))
                    )
                    for preference in preferences
                )
                if local.get("follow_all") and not excluded:
                    via_author = True
                    following_series = True
                if any(
                    preference.get("is_selected")
                    and (
                        (series_asin and preference.get("series_asin") == series_asin)
                        or (local_series and preference.get("series_id") == local_series.get("id"))
                    )
                    for preference in preferences
                ):
                    following_series = True
                    via_author = True
            author["is_followed"] = author_followed
            author["is_following_series"] = following_series
            authors.append(author)
        result["authors"] = authors
        series["is_followed_directly"] = bool(local_series and local_series.get("is_tracked"))
        series["is_followed_via_author"] = via_author
        series["is_followed"] = bool(series["is_followed_directly"] or via_author)
        result["series"] = series
        return result

    def _with_author_follow_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Overlay current local follow selections on a persisted author catalogue."""

        result = dict(payload)
        author = dict(result.get("author") or {})
        matches = [
            local
            for local in list_authors(self.db)
            if local["marketplace"] == self.settings.region
            if self._local_author_matches(author, local)
        ]
        followed = [local for local in matches if local.get("is_tracked")]
        follow_all = any(local.get("follow_all") for local in followed)
        selected_series_asins: set[str] = set()
        excluded_series_asins: set[str] = set()
        selected_book_asins: set[str] = set()
        excluded_book_asins: set[str] = set()
        for local in followed:
            for item in list_author_series_preferences(self.db, local["id"]):
                if not item.get("series_asin"):
                    continue
                if item.get("is_selected"):
                    selected_series_asins.add(str(item["series_asin"]))
                else:
                    excluded_series_asins.add(str(item["series_asin"]))
            for item in list_author_book_preferences(self.db, local["id"]):
                if not item.get("book_asin"):
                    continue
                if item.get("is_selected"):
                    selected_book_asins.add(str(item["book_asin"]))
                else:
                    excluded_book_asins.add(str(item["book_asin"]))

        if matches:
            canonical = next((local for local in matches if local.get("is_tracked")), matches[0])
            author["asin"] = canonical.get("asin") or author.get("asin")
            author["name"] = canonical.get("name") or author.get("name")
            author["image_url"] = author.get("image_url") or canonical.get("image_url") or ""
            author["description"] = author.get("description") or canonical.get("description")
            for key in ("canonical_id", "source_asins", "source_identities", "separate_source_asins", "aliases", "display_name"):
                if canonical.get(key):
                    author[key] = canonical[key]
        result["author"] = author

        groups = []
        for raw_group in result.get("series") or []:
            group = dict(raw_group)
            series_asin = str(group.get("asin") or "")
            group["is_selected"] = bool(
                followed
                and series_asin
                and (
                    (follow_all and series_asin not in excluded_series_asins)
                    or series_asin in selected_series_asins
                )
            )
            books = []
            for raw_book in group.get("books") or []:
                book = dict(raw_book)
                book["is_selected"] = bool(
                    followed
                    and (
                        (
                            follow_all
                            and (
                                (series_asin and series_asin not in excluded_series_asins)
                                or (
                                    not series_asin
                                    and str(book.get("asin") or "") not in excluded_book_asins
                                )
                            )
                        )
                        or (series_asin and series_asin in selected_series_asins)
                        or (not series_asin and str(book.get("asin") or "") in selected_book_asins)
                    )
                )
                books.append(book)
            group["books"] = books
            groups.append(group)
        result["series"] = groups
        result["author_followed"] = bool(followed)
        result["author_follow_all"] = bool(followed and follow_all)
        result["series_filter_active"] = bool(
            followed and (not follow_all or excluded_series_asins or excluded_book_asins)
        )
        return result

    @source_operation("detail")
    async def author_details(
        self,
        asin: str,
        name: str,
        *,
        member_asins: list[str] | None = None,
        member_names: list[str] | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        group_members = []
        for index, member_asin in enumerate(member_asins or []):
            member_asin = str(member_asin or "").strip()
            member_name = str((member_names or [])[index] if index < len(member_names or []) else "").strip()
            if member_asin and member_name:
                group_members.append({"kind": "author", "asin": member_asin, "name": member_name})
        if len({member["asin"] for member in group_members}) > 1:
            builder = AuthorDetailsBuilder(self.db, self.settings, self.audible, self.audnexus)
            return await builder.build_group(
                group_members,
                loader=lambda member_asin, member_name: self.author_details(
                    member_asin, member_name, refresh=refresh,
                ),
            )

        for local in list_authors(self.db):
            if local.get("marketplace") == self.settings.region and asin in source_asins(local):
                asin, name = local["asin"], local["name"]
                break
        result, cached = await self._load_persisted_detail(
            "author",
            asin,
            lambda: AuthorDetailsBuilder(self.db, self.settings, self.audible, self.audnexus).build(asin, name),
            should_persist=lambda payload: not payload.get("error"),
            refresh=refresh,
        )
        entry = self._read_detail_entry("author", asin) if not cached else None
        if not cached:
            for group in result.get("series") or []:
                for raw_book in group.get("books") or []:
                    book = dict(raw_book)
                    target_series = {"asin": group.get("asin"), "name": group.get("name") or "Unnamed series"}
                    book_series = book.get("series") or []
                    if group.get("asin") and not any(series_identity_matches(item, target_series) for item in book_series):
                        if any(
                            isinstance(item, dict)
                            and item.get("asin") and not str(item.get("asin")).startswith("name:")
                            for item in book_series
                        ):
                            continue
                        book["series"] = [
                            *book_series,
                            target_series,
                        ]
                    upsert_book(self.db, book, self.settings.region)
        result = self._with_author_follow_state(result)
        if not cached and not result.get("error"):
            # Persist after discovery/consolidation: a merge invalidates old
            # bibliographies, but this freshly assembled one is complete.
            canonical_asin = result["author"]["asin"]
            self._write_detail_cache("author", canonical_asin, result, fetched_at=entry.fetched_at if entry else None)
            if canonical_asin != asin:
                self._delete_detail_cache("author", asin)
        return result

    @source_operation("detail")
    async def series_details(self, asin: str, name: str = "", seed_asin: str = "", *, refresh: bool = False) -> dict[str, Any]:
        result, cached = await self._load_persisted_detail(
            "series",
            asin,
            lambda: CatalogueDetailsBuilder(self.db, self.settings, self.audible, self.audnexus).series(asin, name, seed_asin),
            refresh=refresh,
        )
        if not cached:
            for raw_book in result.get("books") or []:
                book = dict(raw_book)
                target_series = {"asin": asin, "name": (result.get("series") or {}).get("name") or name or asin}
                book_series = book.get("series") or []
                if not any(series_identity_matches(item, target_series) for item in book_series):
                    if any(
                        isinstance(item, dict)
                        and item.get("asin") and not str(item.get("asin")).startswith("name:")
                        for item in book_series
                    ):
                        continue
                    book["series"] = [
                        *book_series,
                        target_series,
                    ]
                upsert_book(self.db, book, self.settings.region)
        return self._with_series_follow_state(result)

    @source_operation("detail")
    async def book_details(self, asin: str, name: str = "", *, refresh: bool = False) -> dict[str, Any]:
        result, cached = await self._load_persisted_detail(
            "book",
            asin,
            lambda: CatalogueDetailsBuilder(self.db, self.settings, self.audible, self.audnexus).book(asin, name),
            refresh=refresh,
        )
        if isinstance(result.get("book"), dict):
            if not cached:
                upsert_book(self.db, result["book"], self.settings.region)
            local_authors = [author for author in list_authors(self.db, consolidate=not cached)
                             if author["marketplace"] == self.settings.region]
            result["book"] = dict(result["book"])
            result["book"]["authors"] = self._canonicalize_author_records(
                result["book"].get("authors") or [],
                local_authors,
            )
            result["book"]["authors"] = group_search_author_records(result["book"]["authors"])
        return result
