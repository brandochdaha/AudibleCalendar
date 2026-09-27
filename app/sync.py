"""Discovery, tracking and synchronisation orchestration."""

from __future__ import annotations

import asyncio
import weakref
import difflib
import re
import unicodedata
from datetime import datetime
from typing import Any

from .cache import CacheEntry, SourceCache
from .config import Settings
from .author_decisions import read_decisions
from .author_evidence import remember_author_search
from .author_search import _candidate_member, author_merge_candidates, group_search_author_records
from .author_sources import source_asins
from .db import Database
from .instrumentation import SourceMetrics, SyncProgressTracker, source_operation
from .normalization import (
    candidate_from_author,
    candidate_from_series,
    extract_products,
    is_english_language,
    normalize_products,
)
from .repository import list_authors, search_local
from .sources.audible import AudnexusAdapter, AudibleAdapter, SourceError
from .sources.request_cache import capture_source_reads
from .series_reconciliation import SeriesReconciliationMixin
from .sync_catalogue import CatalogueSyncMixin
from .sync_category import CategorySyncMixin
from .sync_details import DetailSyncMixin
from .sync_follow import FollowSyncMixin

def _search_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)).strip()


def _search_rank(query: str, item: dict[str, Any]) -> tuple[int, int, int, float, float, int]:
    query_key = _search_key(query)
    text_key = _search_key(item.get("name") or item.get("title"))
    if not query_key or not text_key:
        return (0, 0, 0, 0.0, 0.0, -len(text_key))
    query_words = set(query_key.split())
    text_words = set(text_key.split())
    coverage = len(query_words & text_words) / len(query_words) if query_words else 0.0
    return (
        int(text_key == query_key),
        int(text_key.startswith(query_key)),
        int(query_key in text_key),
        coverage,
        difflib.SequenceMatcher(None, query_key, text_key).ratio(),
        -abs(len(text_key) - len(query_key)),
    )


def _search_result_rank(query: str, item: dict[str, Any]) -> tuple[int, int, int, float, float, int]:
    if item.get("kind") == "author-match":
        member_ranks = [
            _search_rank(query, member)
            for member in item.get("members") or []
            if isinstance(member, dict)
        ]
        if member_ranks:
            return max(member_ranks)
    return _search_rank(query, item)


def _rank_candidates(query: str, candidates: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        rank = _search_result_rank(query, item)
        return tuple(-value for value in rank) + (
            (item.get("name") or item.get("title") or "").casefold(),
            str(item.get("asin") or ""),
        )

    return sorted(candidates.values(), key=sort_key)


def _is_likely_search_match(query: str, candidate: dict[str, Any]) -> bool:
    """Return whether a series name is strong enough to stop pagination."""

    rank = _search_rank(query, candidate)
    return bool(
        rank[0]
        or rank[1]
        or rank[2]
        or (rank[3] >= 0.75 and rank[4] >= 0.8)
    )


def _append_unique_relations(candidate: dict[str, Any], relation: str, items: list[dict[str, Any]]) -> None:
    """Add relationships already present in a search response without refetching them."""

    def identity(item: dict[str, Any]) -> tuple[str, str]:
        asin = str(item.get("asin") or "").strip()
        return ("asin", asin) if asin else ("name", _search_key(item.get("name") or item.get("title")))

    existing = candidate.setdefault(relation, [])
    seen = {identity(item) for item in existing if isinstance(item, dict)}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = identity(item)
        if key in seen:
            continue
        existing.append(dict(item))
        seen.add(key)


def _separation_decisions(decisions: dict[tuple[str, str, str], str]) -> dict[tuple[str, str, str], str]:
    return {key: value for key, value in decisions.items() if value == "separate"}


def _author_search_results(
    query: str,
    authors: list[dict[str, Any]],
    books_by_author: dict[str, set[str]],
    decisions: dict[tuple[str, str, str], str] | None = None,
    min_shared_series: int = 1,
) -> list[dict[str, Any]]:
    decisions = decisions or {}
    grouped = group_search_author_records(authors, books_by_author, decisions)
    candidates = author_merge_candidates(
        grouped,
        books_by_author,
        decisions,
        min_shared_series=min_shared_series,
    )
    candidate_sources = {
        asin
        for candidate in candidates
        for member in candidate.get("members") or []
        for asin in source_asins(member)
    }
    visible_authors = [
        {**_candidate_member(author, books_by_author), **author} for author in grouped
        if not source_asins(author) & candidate_sources
    ]
    combined = [*visible_authors, *candidates]
    return _rank_candidates(
        query,
        {
            (str(item.get("kind") or "author"), str(item.get("asin") or index)): item
            for index, item in enumerate(combined)
        },
    )[:50]


class SyncService(SeriesReconciliationMixin, DetailSyncMixin, CategorySyncMixin, FollowSyncMixin, CatalogueSyncMixin):
    def __init__(self, db: Database, settings: Settings, *, source_cache=None, http_pool=None):
        self.db = db
        self.settings = settings
        self.metrics = SourceMetrics()
        self.source_cache = SourceCache(db)
        adapter_options = {
            "metrics": self.metrics,
            "cache": source_cache,
            "product_cache": self.source_cache,
            "http_pool": http_pool,
        }
        self.audible = AudibleAdapter(settings, **adapter_options)
        self.audnexus = AudnexusAdapter(settings, **adapter_options)
        # A sync can be started manually while the scheduled job is running.
        # Serialise those runs so they do not duplicate every upstream call.
        self._sync_lock = asyncio.Lock()
        self._sync_task = None
        # This cache is deliberately scoped to one sync run. It removes
        # duplicate requests within a run without making catalogue data stale
        # across separate user-initiated syncs.
        self._sync_cache: dict[tuple[Any, ...], tuple[bool, Any]] = {}
        self._sync_products: dict[str, CacheEntry] = {}
        # Normalized products retain their raw source_payload. The response
        # cache keeps those objects alive until the run ends.
        self._sync_product_times: dict[int, datetime] = {}
        self._sync_started_at: datetime | None = None
        self._sync_force = False
        self._detail_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self.progress = SyncProgressTracker(self.metrics)
        self._reconcile_missing_series()

    def sync_progress(self) -> dict[str, Any] | None:
        return self.progress.snapshot()

    def source_metrics(self) -> dict[str, int]:
        return self.metrics.snapshot()

    def source_metric_breakdown(self) -> dict[str, dict[str, dict[str, int]]]:
        return self.metrics.breakdown()

    async def _cached_sync_call(self, key: tuple[Any, ...], operation: Any) -> Any:
        if self._sync_started_at is None:
            return await operation()
        if key[0] == "get_product":
            observed = self._sync_products.get(str(key[1]))
            if (self.source_cache.is_fresh(observed, self._detail_max_age("book", str(key[1])))
                    and (not self._sync_force or observed.fetched_at >= self._sync_started_at)):
                self.metrics.increment("sync_deduplications")
                return {"product": observed.payload}
        cached = self._sync_cache.get(key)
        if cached is not None:
            self.metrics.increment("sync_deduplications")
            succeeded, value = cached
            if succeeded:
                return value
            raise value
        try:
            with capture_source_reads() as reads:
                value = await operation()
                if reads.stale:
                    raise SourceError("Audible could not be checked; previously cached results remain available")
        except SourceError as exc:
            self._sync_cache[key] = (False, exc)
            raise
        self._sync_cache[key] = (True, value)
        if key[0] in {"search_products_page", "get_similar", "get_product"} and reads.fetched_at:
            for product in extract_products(value):
                self._sync_product_times[id(product)] = reads.fetched_at
                asin = str(product.get("asin") or "")
                # ASIN-only responses cannot replace a full product request.
                if not asin or not product.get("title"):
                    continue
                previous = self._sync_products.get(asin)
                if previous is None or reads.fetched_at > previous.fetched_at:
                    self._sync_products[asin] = CacheEntry(product, reads.fetched_at)
        return value

    def _sync_product_observed_at(self, product: dict[str, Any]) -> datetime | None:
        return self._sync_product_times.get(id(product.get("source_payload")))

    @source_operation("search")
    async def search(self, query: str, kind: str) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        candidates: dict[tuple[str, str], dict[str, Any]] = {}
        books_by_author: dict[str, set[str]] = {}
        if self.settings.demo_data:
            local_candidates = self._local_candidates(query, kind)
            if local_candidates:
                if kind == "author":
                    local_records = list(local_candidates.values())
                    marketplaces = {
                        item.get("marketplace")
                        for item in local_records
                        if item.get("marketplace")
                    }
                    with self.db.transaction() as connection:
                        decisions = read_decisions(connection)
                    local_authors = self._canonicalize_author_records(
                        local_records,
                        [
                            author
                            for author in list_authors(self.db)
                            if author["marketplace"] in (marketplaces or {self.settings.region})
                        ],
                        decisions=_separation_decisions(decisions),
                    )
                    return _author_search_results(
                        query,
                        local_authors,
                        {},
                        decisions,
                        min_shared_series=getattr(self.settings, "author_merge_min_shared_series", 1),
                    )
                return _rank_candidates(query, local_candidates)[:50]
        try:
            payload = await self.audible.search_products(query, kind)
            fetched_product_count = 0
            last_page_size = 0
            reported_total: int | None = None

            def add_products(raw_payload: Any) -> None:
                nonlocal fetched_product_count, last_page_size, reported_total
                raw_products = extract_products(raw_payload)
                last_page_size = len(raw_products)
                fetched_product_count += last_page_size
                if isinstance(raw_payload, dict):
                    try:
                        total = int(raw_payload.get("total_results"))
                    except (TypeError, ValueError):
                        total = 0
                    if total > 0:
                        reported_total = max(reported_total or 0, total)

                for product in normalize_products(raw_payload, self.settings.region, english_only=self.settings.english_only):
                    if kind == "book":
                        candidate = {
                            "kind": "book",
                            "asin": product["asin"],
                            "name": product["title"],
                            "title": product["title"],
                            "release_date": product["release_date"],
                            "image_url": product["image_url"],
                            "authors": product["authors"],
                            "series": product["series"],
                            "source": "audible",
                            "payload": product["source_payload"],
                        }
                        candidates[(kind, candidate["asin"])] = candidate
                    elif kind == "author":
                        for author in product["authors"]:
                            books_by_author.setdefault(str(author["asin"]), set()).add(product["asin"])
                            candidate = {
                                **candidate_from_author(author, "audible"),
                                "marketplace": self.settings.region,
                            }
                            key = (kind, candidate["asin"])
                            candidate = {**candidates.get(key, {}), **candidate}
                            _append_unique_relations(candidate, "series", product["series"])
                            candidates[key] = candidate
                    else:
                        for series in product["series"]:
                            candidate = candidate_from_series(series, product["asin"])
                            key = (kind, candidate["asin"])
                            candidate = {**candidates.get(key, {}), **candidate}
                            _append_unique_relations(candidate, "authors", product["authors"])
                            candidates[key] = candidate

            add_products(payload)
            if kind == "series" and not any(_is_likely_search_match(query, item) for item in candidates.values()):
                for page in range(1, self.settings.sync_max_pages):
                    page_payload = await self.audible.search_products_page(query, kind, page)
                    if not extract_products(page_payload):
                        self.metrics.increment("pagination_empty")
                        break
                    add_products(page_payload)
                    if any(_is_likely_search_match(query, item) for item in candidates.values()):
                        break
                    if len(extract_products(page_payload)) < self.settings.sync_page_size:
                        self.metrics.increment("pagination_short")
                        break
                else:
                    self.metrics.increment("pagination_limit")

            relationships_incomplete = (
                reported_total > fetched_product_count
                if reported_total is not None
                else last_page_size >= self.settings.sync_page_size
            )
            if relationships_incomplete and kind in {"author", "series"}:
                likely_candidates = [item for item in candidates.values() if _is_likely_search_match(query, item)]
                for candidate in likely_candidates or candidates.values():
                    candidate["relationships_incomplete"] = True
        except SourceError:
            # Local records remain searchable when no account is configured or an upstream is down.
            candidates.update(self._local_candidates(query, kind))
        if not candidates:
            # Some public catalogue responses are successful but empty when no
            # credentials are supplied. Keep the local demo/data store useful in
            # that case too.
            candidates.update(self._local_candidates(query, kind))
        if kind == "author":
            remember_author_search(self.db, self.settings.region, list(candidates.values()), books_by_author)
            with self.db.transaction() as connection:
                decisions = read_decisions(connection)
            authors = self._canonicalize_author_records(
                list(candidates.values()),
                [author for author in list_authors(self.db) if author["marketplace"] == self.settings.region],
                books_by_author,
                _separation_decisions(decisions),
            )
            return _author_search_results(
                query,
                authors,
                books_by_author,
                decisions,
                min_shared_series=getattr(self.settings, "author_merge_min_shared_series", 1),
            )
        return _rank_candidates(query, candidates)[:50]

    def _local_candidates(self, query: str, kind: str) -> dict[tuple[str, str], dict[str, Any]]:
        local: dict[tuple[str, str], dict[str, Any]] = {}
        for item in search_local(self.db, query, kind):
            if kind == "book" and self.settings.english_only and not is_english_language(item.get("language")):
                continue
            if kind == "book":
                candidate = {
                    "kind": kind,
                    "asin": item["asin"],
                    "name": item.get("title"),
                    "title": item.get("title"),
                    "release_date": item.get("release_date"),
                    "image_url": item.get("image_url"),
                    "source": "local",
                    "payload": item.get("source_payload") or item,
                }
            else:
                candidate = {
                    "kind": kind,
                    "asin": item["asin"],
                    "name": item.get("name"),
                    "marketplace": item.get("marketplace") or self.settings.region,
                    "image_url": item.get("image_url", ""),
                    "source": "local",
                    "payload": item.get("source_payload") or item,
                }
                for key in (
                    "id", "canonical_id", "source_asins", "source_identities",
                    "separate_source_asins", "aliases", "display_name",
                ):
                    if item.get(key) not in (None, "", [], {}):
                        candidate[key] = item[key]
            local[(kind, item["asin"])] = candidate
        return local
