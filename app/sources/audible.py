"""Audible public-catalogue adapter."""

from __future__ import annotations

from typing import Any
from contextlib import nullcontext

import httpx

from ..cache import SourceCache
from ..config import Settings
from ..db import Database
from ..instrumentation import SourceMetrics
from ..time_zone import local_today
from .http import HttpPool
from .request_cache import SourceError, cached_response


_DOMAINS = {
    "us": "com",
    "uk": "co.uk",
    "ca": "ca",
    "au": "com.au",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "it": "it",
    "jp": "co.jp",
    "in": "co.in",
    "br": "com.br",
}

# Category overviews are deliberately manual and infrequent.  A cached page
# remains usable until the user explicitly refreshes the overview; the large
# value here is a safety net rather than a scheduled refresh policy.
_CATEGORY_CACHE_TTL_SECONDS = 10 * 365 * 24 * 60 * 60
_CATEGORY_PAGE_SIZE = 50


class _CachedSourceAdapter:
    def __init__(
        self,
        settings: Settings,
        db: Database | None = None,
        metrics: SourceMetrics | None = None,
        cache: SourceCache | None = None,
        http_pool: HttpPool | None = None,
        product_cache: SourceCache | None = None,
    ):
        self.settings = settings
        self._product_cache = product_cache or (SourceCache(db) if db else None)
        self._cache = cache or self._product_cache
        self.http_pool = http_pool or HttpPool()
        self._metrics = metrics


class AudibleAdapter(_CachedSourceAdapter):

    @property
    def base_url(self) -> str:
        return f"https://api.audible.{_DOMAINS.get(self.settings.region, 'com')}"

    async def _direct_get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            response = await self.http_pool.client().get(f"{self.base_url}/{path.lstrip('/')}", params=params, timeout=self.settings.audible_timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            delay = 30
            if status in (403, 429) or status >= 500:
                delay = self.http_pool.defer_audible(exc.response.headers.get("Retry-After"),
                                                     default=60 if status in (403, 429) else 30)
            raise SourceError(f"Audible returned HTTP {status}", retry_after=delay) from exc
        except (httpx.HTTPError, ValueError) as exc:
            delay = self.http_pool.defer_audible()
            raise SourceError(f"Audible request failed: {exc}", retry_after=delay) from exc

    async def get(
        self,
        path: str,
        *,
        force: bool = False,
        cache_ttl_seconds: float = 0.0,
        **params: Any,
    ) -> Any:
        async def request() -> Any:
            async with self.http_pool.audible_request():
                if self._metrics:
                    self._metrics.record_network_request("audible")
                try:
                    return await self._direct_get(path, params)
                except SourceError:
                    if self._metrics:
                        self._metrics.increment("request_errors")
                    raise

        family = ("category_page" if "category_id" in params else
                  "catalog_search" if path.endswith("/products") else
                  "similar" if path.endswith("/sims") else "product")
        with self._metrics.family(family) if self._metrics else nullcontext():
            return await cached_response(
                self._cache, self._product_cache, "audible",
                {"region": self.settings.region, "path": path, "params": params},
                cache_ttl_seconds, request, force=force, metrics=self._metrics,
            )

    async def search_products(
        self,
        query: str,
        kind: str,
        *,
        force: bool = False,
        cache_ttl_seconds: float | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "num_results": self.settings.sync_page_size,
            "page": 0,
            "products_sort_by": "-ReleaseDate",
            "response_groups": "contributors,series,product_attrs,product_desc,media,sku",
        }
        if kind == "author":
            params["author"] = query
        else:
            params["keywords"] = query
        return await self.get(
            "1.0/catalog/products",
            force=force,
            cache_ttl_seconds=cache_ttl_seconds or self.settings.cache_search_minutes * 60,
            **params,
        )

    async def search_products_page(
        self,
        query: str,
        kind: str,
        page: int,
        *,
        force: bool = False,
        cache_ttl_seconds: float | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "num_results": self.settings.sync_page_size,
            "page": page,
            "products_sort_by": "-ReleaseDate",
            "response_groups": "contributors,series,product_attrs,product_desc,media,sku",
        }
        params["author" if kind == "author" else "keywords"] = query
        return await self.get(
            "1.0/catalog/products",
            force=force,
            cache_ttl_seconds=cache_ttl_seconds or self.settings.cache_search_minutes * 60,
            **params,
        )

    async def get_category_products_page(
        self,
        category_id: str,
        page: int,
        *,
        force: bool = False,
        cache_ttl_seconds: float | None = None,
        sort_by: str = "-ReleaseDate",
    ) -> Any:
        """Return one cached page of products for an Audible genre category."""

        params: dict[str, Any] = {
            "category_id": category_id,
            "num_results": _CATEGORY_PAGE_SIZE,
            "page": page,
            "products_sort_by": sort_by,
            "response_groups": "contributors,series,product_attrs,product_desc,media,sku",
        }
        return await self.get(
            "1.0/catalog/products",
            force=force,
            cache_ttl_seconds=cache_ttl_seconds or _CATEGORY_CACHE_TTL_SECONDS,
            **params,
        )

    async def get_product(self, asin: str, *, force: bool = False) -> Any:
        max_age = (
            self._product_cache.product_max_age_seconds(
                asin,
                self.settings.region,
                upcoming_hours=self.settings.cache_upcoming_hours,
                far_future_hours=self.settings.cache_far_future_hours,
                released_days=self.settings.cache_released_days,
                today=local_today(self.settings.time_zone),
            )
            if self._product_cache
            else self.settings.cache_upcoming_hours * 60 * 60
        )
        return await self.get(
            f"1.0/catalog/products/{asin}",
            force=force,
            cache_ttl_seconds=max_age,
            response_groups="contributors,series,product_attrs,product_desc,media,sku",
        )

    async def get_similar(self, asin: str, *, force: bool = False) -> Any:
        return await self.get(
            f"1.0/catalog/products/{asin}/sims",
            force=force,
            cache_ttl_seconds=self.settings.cache_series_hours * 60 * 60,
            similarity_type="InTheSameSeries",
            num_results=50,
            response_groups="contributors,series,product_attrs,product_desc,media,sku",
        )


class AudnexusAdapter(_CachedSourceAdapter):
    async def _get(
        self,
        path: str,
        *,
        force: bool = False,
        cache_ttl_seconds: float = 0.0,
        **params: Any,
    ) -> Any:
        if not self.settings.audnexus_enabled:
            return None
        async def request() -> Any:
            if self._metrics:
                self._metrics.record_network_request("audnexus")
            try:
                response = await self.http_pool.client().get(f"{self.settings.audnexus_base_url}/{path.lstrip('/')}", params=params, timeout=self.settings.audible_timeout)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None
                if self._metrics:
                    self._metrics.increment("request_errors")
                raise SourceError(f"Audnexus returned HTTP {exc.response.status_code}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                if self._metrics:
                    self._metrics.increment("request_errors")
                raise SourceError(f"Audnexus request failed: {exc}") from exc

        with self._metrics.family("audnexus_book") if self._metrics else nullcontext():
            return await cached_response(
                self._cache, self._product_cache, "audnexus",
                {"base_url": self.settings.audnexus_base_url, "path": path, "params": params},
                cache_ttl_seconds, request, force=force, metrics=self._metrics,
            )

    async def get_book(self, asin: str, region: str, *, force: bool = False) -> dict[str, Any] | None:
        max_age = (
            self._product_cache.product_max_age_seconds(
                asin,
                region,
                upcoming_hours=self.settings.cache_upcoming_hours,
                far_future_hours=self.settings.cache_far_future_hours,
                released_days=self.settings.cache_released_days,
                today=local_today(self.settings.time_zone),
            )
            if self._product_cache
            else self.settings.cache_upcoming_hours * 60 * 60
        )
        result = await self._get(
            f"/books/{asin}",
            force=force,
            cache_ttl_seconds=max_age,
            region=region,
        )
        return result if isinstance(result, dict) else None
