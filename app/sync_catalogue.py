"""Full followed-catalogue synchronization workflow."""

from __future__ import annotations

from .author_discovery import resolve_catalogue_author
from .author_sources import author_search_names

import asyncio
from starlette.concurrency import run_in_threadpool
from datetime import date, datetime, timezone
from typing import Any

from .catalogue_snapshot import CatalogueSnapshot
from .normalization import (
    explicit_author_asins,
    extract_products,
    infer_missing_series,
    infer_series_item_from_title,
    is_followable_author,
    normalize_audnexus_book,
    normalize_products,
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
from .instrumentation import source_operation


from .sync_targets import CatalogueTargetsMixin


class CatalogueSyncMixin(CatalogueTargetsMixin):
    async def _enrich_book_from_audnexus(
        self,
        product: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Hydrate one known book ASIN without changing Audible discovery."""

        asin = str(product.get("asin") or "").strip()
        if not asin or not self.settings.audnexus_enabled:
            return product
        try:
            payload = await self._cached_sync_call(
                ("audnexus_book", asin),
                lambda: self.audnexus.get_book(asin, self.settings.region, force=force),
            )
        except SourceError:
            # Audnexus is enrichment, not a reason to lose an otherwise valid
            # Audible catalogue result.
            return product
        if not payload:
            return product
        enriched = normalize_audnexus_book(payload, self.settings.region)
        return enrich_missing_fields(product, enriched)

    def sync_scope(self) -> str:
        """Return the catalogue scope used by an automatic or manual sync."""

        if self.settings.automatic_sync_enabled:
            return "followed"
        if self.settings.favourites_sync_enabled:
            return "favourites"
        # Manual Sync retains the old behaviour when both automatic options
        # are off: it still provides an explicit full followed-catalogue check.
        return "followed"

    @staticmethod
    def _covered_by_favourite_author(item: dict[str, Any], relation_key: str) -> bool:
        return any(
            author.get("is_favourite") and author.get("follow_all")
            for author in item.get(relation_key) or []
            if isinstance(author, dict)
        )

    async def sync_all(self, force: bool = False, scope: str | None = None) -> dict[str, Any]:
        if self._sync_task is None or self._sync_task.done():
            self._sync_task = asyncio.create_task(self._run_sync(force, scope))
            # Observe exceptions even if the HTTP caller disconnects.
            self._sync_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(self._sync_task)

    @source_operation(lambda self, force=False, scope=None: "sync_" + (scope or self.sync_scope()))
    async def _run_sync(self, force: bool = False, scope: str | None = None) -> dict[str, Any]:
        sync_scope = scope or self.sync_scope()
        if sync_scope not in {"followed", "favourites"}:
            raise ValueError("Unknown sync scope")
        async with self._sync_lock:
            self.progress.start(force, operation="sync_" + sync_scope)
            await run_in_threadpool(mark_sync_started, self.db)
            self._sync_cache = {}
            self._sync_products = {}
            self._sync_product_times = {}
            self._sync_started_at = datetime.now(timezone.utc)
            self._sync_force = force
            try:
                result = await self._sync_all(force=force, scope=sync_scope)
            except asyncio.CancelledError:
                await run_in_threadpool(mark_sync_completed, self.db, "interrupted", error="Sync interrupted during shutdown")
                raise
            except Exception as exc:
                metrics = self.progress.metrics_since_start()
                await run_in_threadpool(mark_sync_completed, self.db, "error", error=str(exc), metrics=metrics)
                raise
            else:
                metrics = self.progress.metrics_since_start()
                result["metrics"] = metrics
                result["sync_scope"] = sync_scope
                errors = result.get("errors") or []
                await run_in_threadpool(mark_sync_completed,
                    self.db,
                    str(result.get("status") or "ok"),
                    error="\n".join(str(error) for error in errors) or None,
                    metrics=metrics,
                )
                return result
            finally:
                self._sync_cache.clear()
                self._sync_products.clear()
                self._sync_product_times.clear()
                self._sync_started_at = None
                self._sync_force = False
                self.progress.clear()

    @property
    def sync_in_progress(self) -> bool:
        return self._sync_lock.locked() or (self._sync_task is not None and not self._sync_task.done())

    async def _sync_all(self, force: bool = False, scope: str = "followed") -> dict[str, Any]:
        totals = {"authors": 0, "series": 0, "books": 0, "errors": []}
        # Demo records are deliberately local fixtures. Real tracked records
        # use the public Audible catalogue adapter.
        authors = [author for author in (await run_in_threadpool(list_authors, self.db, tracked_only=True)) if author["marketplace"] != "demo"]
        # A followed author can be narrowed to an explicit set of series. In
        # that mode the selected series are the authoritative sync targets;
        # querying the whole author would both over-fetch and risk missing a
        # newly indexed volume that only appears in the series search.
        snapshot = await run_in_threadpool(CatalogueSnapshot.load, self.db)
        await run_in_threadpool(self._reconcile_missing_series, snapshot)
        today = local_today(self.settings.time_zone)
        if scope == "favourites":
            broad_authors = [
                author
                for author in authors
                if author.get("follow_all") and author.get("is_favourite")
            ]
            followed_series = [
                item
                for item in (await run_in_threadpool(list_followed_series, self.db, include_sync_metadata=True))
                if item["marketplace"] != "demo"
            ]
            series = [
                item
                for item in followed_series
                if item.get("is_favourite")
                and not self._covered_by_favourite_author(item, "followed_authors")
            ]
            selected_book_ids = (await run_in_threadpool(list_selected_author_book_ids, self.db))
            books = [
                book
                for book in (await run_in_threadpool(list_followed_books, self.db))
                if book["marketplace"] != "demo"
                and book.get("is_favourite")
                and not book.get("series")
                and (book.get("is_tracked") or book.get("id") in selected_book_ids)
                and not self._covered_by_favourite_author(book, "authors")
                and not is_past_release(book, today)
            ]
        else:
            broad_authors = [author for author in authors if author.get("follow_all")]
            tracked_series = [item for item in (await run_in_threadpool(list_series, self.db, tracked_only=True)) if item["marketplace"] != "demo"]
            selected_series = [item for item in (await run_in_threadpool(list_selected_author_series, self.db)) if item["marketplace"] != "demo"]
            series_by_id = {item["id"]: item for item in tracked_series}
            series_by_id.update({item["id"]: item for item in selected_series})
            series = list(series_by_id.values())
            tracked_books = [book for book in snapshot.books if book.get("is_tracked") and book["marketplace"] != "demo"]
            selected_book_ids = (await run_in_threadpool(list_selected_author_book_ids, self.db))
            books_by_id = {book["id"]: book for book in tracked_books}
            books_by_id.update({
                book["id"]: book
                for book in snapshot.books
                if book.get("id") in selected_book_ids and book["marketplace"] != "demo"
            })
            books = [book for book in books_by_id.values() if not is_past_release(book, today)]
        self.progress.set_plan(len(broad_authors) + len(series) + len(books))
        for author in broad_authors:
            self.progress.start_target("author", str(author.get("name") or author.get("asin") or "Unknown author"))
            try:
                count = await self._sync_author(author, snapshot, force=force)
                totals["books"] += count
            except SourceError as exc:
                totals["errors"].append(f"Author {author['name']}: {exc}")
                (await run_in_threadpool(update_sync_state, self.db, "author", author["id"], error=str(exc)))
            except Exception as exc:
                (await run_in_threadpool(update_sync_state, self.db, "author", author["id"], error=str(exc)))
                raise
            finally:
                self.progress.complete_target()
        for series in series:
            self.progress.start_target("series", str(series.get("name") or series.get("asin") or "Unknown series"))
            try:
                count = await self._sync_series(series, snapshot, force=force)
                totals["books"] += count
            except SourceError as exc:
                totals["errors"].append(f"Series {series['name']}: {exc}")
                (await run_in_threadpool(update_sync_state, self.db, "series", series["id"], error=str(exc)))
            except Exception as exc:
                (await run_in_threadpool(update_sync_state, self.db, "series", series["id"], error=str(exc)))
                raise
            finally:
                self.progress.complete_target()
        for book in books:
            self.progress.start_target("book", str(book.get("title") or book.get("asin") or "Unknown book"))
            try:
                await self._sync_book(book, snapshot, force=force)
                totals["books"] += 1
            except SourceError as exc:
                totals["errors"].append(f"Book {book['title']}: {exc}")
                (await run_in_threadpool(update_sync_state, self.db, "book", book["id"], error=str(exc)))
            except Exception as exc:
                (await run_in_threadpool(update_sync_state, self.db, "book", book["id"], error=str(exc)))
                raise
            finally:
                self.progress.complete_target()
        totals["status"] = "ok" if not totals["errors"] else "partial"
        scope_label = "Favorited catalog" if scope == "favourites" else "Followed catalog"
        if not (broad_authors or series or books):
            totals["message"] = f"No {scope_label.lower()} targets are available yet."
        elif scope == "favourites":
            totals["message"] = "Favorited catalog sync complete" if not totals["errors"] else "Favorited catalog sync completed with errors"
        else:
            totals["message"] = "Sync complete" if not totals["errors"] else "Sync completed with errors"
        return totals
