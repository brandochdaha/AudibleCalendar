"""Repair cached standalone books using confirmed local catalogue evidence."""

from __future__ import annotations

from .author_sources import source_asins
from .catalogue_snapshot import CatalogueSnapshot
from .normalization import infer_missing_series, series_sequence_from_title
from .repository import attach_missing_book_series, list_authors
from .repository.presentation import summaries


class SeriesReconciliationMixin:
    def _reconcile_missing_series(self, snapshot: CatalogueSnapshot | None = None) -> int:
        # Startup uses compact records and never calls an upstream source.
        # Sync already has a snapshot, so reuse it instead of reloading books.
        if snapshot is None:
            with summaries():
                snapshot = CatalogueSnapshot.load(self.db)
        books = [book for book in snapshot.books
                 if book.get("marketplace") == self.settings.region]
        candidates = [
            {**book, "series": []} for book in books
            if not book.get("series") and book.get("authors")
            and series_sequence_from_title(book.get("title"))[1] is not None
        ]
        if not candidates:
            return 0
        credited_asins = {str(author.get("asin") or "")
                          for book in candidates for author in book["authors"]}
        with summaries():
            authors = [author for author in list_authors(self.db, consolidate=False)
                       if author.get("marketplace") == self.settings.region
                       and source_asins(author) & credited_asins]
        infer_missing_series(candidates, author_identities=authors, known_products=books)
        repaired = 0
        for book in candidates:
            if not book["series"]:
                continue
            previous = snapshot.get(book["asin"])
            if attach_missing_book_series(self.db, book["id"], book["series"]):
                # Repository reconciliation promotes direct book follows to
                # series follows; later sync targets must see that promotion.
                book["is_tracked"] = False
                snapshot.books_by_asin[book["asin"]] = book
                self._refresh_detail_caches_for_product(book, previous, local_only=True)
                repaired += 1
        return repaired
