"""In-memory catalogue view shared by every target in one sync run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Database
from .repository import list_books


@dataclass
class CatalogueSnapshot:
    books_by_asin: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, database: Database) -> "CatalogueSnapshot":
        books = list_books(database, consolidate=False)
        return cls({str(book.get("asin") or ""): book for book in books if book.get("asin")})

    @property
    def books(self) -> list[dict[str, Any]]:
        return list(self.books_by_asin.values())

    def get(self, asin: Any) -> dict[str, Any] | None:
        return self.books_by_asin.get(str(asin or ""))

    def for_series(self, asin: Any) -> list[dict[str, Any]]:
        series_asin = str(asin or "")
        return [
            book
            for book in self.books_by_asin.values()
            if any(str(item.get("asin") or "") == series_asin for item in book.get("series") or [])
        ]

    def remember(
        self,
        product: dict[str, Any],
        *,
        book_id: int | None = None,
        tracked: bool = False,
    ) -> dict[str, Any]:
        """Reflect an upsert so later targets see discoveries from this run."""

        asin = str(product.get("asin") or "")
        if not asin:
            raise ValueError("snapshot product requires an ASIN")
        previous = self.books_by_asin.get(asin) or {}
        merged = dict(previous)
        for key, value in product.items():
            if value not in (None, "", []):
                merged[key] = value
        if book_id is not None:
            merged["id"] = book_id
        merged["is_tracked"] = bool(tracked or previous.get("is_tracked"))
        self.books_by_asin[asin] = merged
        return merged
