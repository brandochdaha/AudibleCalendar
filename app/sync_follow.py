"""Follow-intent mutations shared by the HTTP facade and sync engine."""

from __future__ import annotations

from typing import Any

from .author_discovery import resolve_catalogue_author
from .author_sources import source_asins
from .catalogue_snapshot import CatalogueSnapshot

from .normalization import (
    infer_missing_series,
    is_followable_author,
    normalize_audnexus_book,
    normalize_product,
    normalize_products,
    series_sequence_from_title,
    series_identity_matches,
)
from .repository import (
    list_authors,
    clear_author_release_selections,
    set_author_follow_all,
    set_author_book_preference,
    set_author_series_preference,
    upsert_author,
    upsert_book,
    upsert_series,
)
from .source_precedence import enrich_missing_fields
from .repository.presentation import summaries
from .sources.audible import SourceError
from .instrumentation import source_operation
from .sync_matching import author_candidate_matches


class FollowSyncMixin:
    def _follow_series_authors(
        self,
        authors: list[dict[str, Any]],
        series_id: int,
        marketplace: str,
    ) -> list[dict[str, Any]]:
        """Follow authors associated with a series, scoped to that series."""

        records: dict[str, dict[str, Any]] = {}
        for author in authors:
            if not is_followable_author(author) or not author.get("asin"):
                continue
            records.setdefault(str(author["asin"]), author)
        for author in records.values():
            author_id = upsert_author(
                self.db,
                {**author, "follow_all": False},
                marketplace,
                tracked=True,
            )
            set_author_series_preference(
                self.db,
                author_id,
                series_id,
                True,
                preserve_follow_all=True,
            )
        return list(records.values())

    @source_operation("follow")
    async def add_watch(self, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        marketplace = item.get("marketplace") or self.settings.region
        if marketplace != self.settings.region and not (self.settings.demo_data and marketplace == "demo"):
            raise ValueError("This item belongs to another region; refresh the page before following it")
        if kind == "author":
            author = {
                "asin": item.get("asin"),
                "name": item.get("name"),
                "description": item.get("description"),
                "image_url": item.get("image_url"),
                "audnexus_asin": item.get("audnexus_asin") or item.get("asin"),
                "payload": item.get("payload") or item,
            }
            if not is_followable_author(author):
                raise ValueError("Contributor records cannot be followed")
            entity_id = upsert_author(
                self.db,
                {**author, "follow_all": True},
                marketplace,
                tracked=True,
            )
            # An explicit author follow means all of the author's releases;
            # clear any narrower series scope that may have been created when
            # the author was inherited from a series or book follow.
            set_author_follow_all(self.db, entity_id, True)
            if not self.settings.demo_data:
                try:
                    initial_payload = await self.audible.search_products(author["name"], "author")
                    initial_products = []
                    products = normalize_products(initial_payload, marketplace, english_only=self.settings.english_only)
                    author = next(local for local in list_authors(self.db)
                                  if local["marketplace"] == marketplace and author["asin"] in source_asins(local))
                    author = resolve_catalogue_author(self.db, author, products, marketplace)
                    entity_id = author["id"]
                    for product in products:
                        matches = any(author_candidate_matches(person, author) for person in product["authors"])
                        if matches:
                            initial_products.append(product)
                    known_products = []
                    if any(not product["series"] and series_sequence_from_title(product.get("title"))[1] is not None
                           for product in initial_products):
                        with summaries():
                            known_products = [book for book in CatalogueSnapshot.load(self.db).books
                                              if book.get("marketplace") == marketplace]
                    infer_missing_series(initial_products, author_identities=[author], known_products=known_products)
                    for product in initial_products:
                        upsert_book(self.db, product, marketplace)
                except SourceError:
                    pass
            return {"kind": kind, "id": entity_id, "asin": author["asin"]}
        if kind == "series":
            series = {
                "asin": item.get("asin"),
                "name": item.get("name"),
                "is_verified": bool(item.get("asin")),
                "payload": item.get("payload") or item,
            }
            entity_id = upsert_series(self.db, series, marketplace, tracked=True)
            seed_asin = item.get("seed_asin")
            if seed_asin:
                try:
                    seed_payload = await self.audible.get_product(str(seed_asin))
                    seed_products = normalize_products(seed_payload, marketplace, english_only=self.settings.english_only)
                    if seed_products:
                        upsert_book(self.db, seed_products[0], marketplace)
                except SourceError:
                    # Following the series is still useful even if the seed
                    # product cannot be hydrated at this moment.
                    pass
            series_authors = [author for author in item.get("authors") or [] if isinstance(author, dict)]
            for raw_book in item.get("books") or []:
                if isinstance(raw_book, dict):
                    raw_series = [series_item for series_item in raw_book.get("series") or [] if isinstance(series_item, dict)]
                    selected_series = any(series_identity_matches(series_item, series) for series_item in raw_series)
                    has_other_stable_series = any(
                        series_item.get("asin")
                        and not str(series_item.get("asin")).startswith("name:")
                        for series_item in raw_series
                    )
                    if raw_series and not selected_series and has_other_stable_series:
                        continue
                    series_authors.extend(author for author in raw_book.get("authors") or [] if isinstance(author, dict))
            author_records = self._follow_series_authors(series_authors, entity_id, marketplace)

            for raw_book in item.get("books") or []:
                if not isinstance(raw_book, dict) or not raw_book.get("asin"):
                    continue
                book = dict(raw_book)
                book_authors = [author for author in book.get("authors") or [] if isinstance(author, dict)]
                for author in author_records:
                    if not any(
                        author_candidate_matches(person, author)
                        for person in book_authors
                    ) and (
                        not book_authors
                        or not any(
                            person.get("asin")
                            and not str(person.get("asin")).startswith("name:")
                            for person in book_authors
                        )
                    ):
                        book_authors.append({"asin": author["asin"], "name": author["name"]})
                book_series = [series_item for series_item in book.get("series") or [] if isinstance(series_item, dict)]
                if not any(series_identity_matches(series_item, series) for series_item in book_series):
                    if any(
                        series_item.get("asin")
                        and not str(series_item.get("asin")).startswith("name:")
                        for series_item in book_series
                    ):
                        continue
                    book_series.append({"asin": series["asin"], "name": series["name"]})
                book["authors"] = book_authors
                book["series"] = book_series
                upsert_book(self.db, book, marketplace)
            return {"kind": kind, "id": entity_id, "asin": series["asin"]}
        if kind == "book":
            product = None
            if item.get("payload") and item["payload"].get("asin"):
                product = normalize_product(item["payload"], marketplace)
            if not product or not product["asin"]:
                if not (self.settings.demo_data and item.get("title")):
                    try:
                        product_payload = await self.audible.get_product(str(item.get("asin")))
                        products = normalize_products(product_payload, marketplace, english_only=self.settings.english_only)
                        product = products[0] if products else None
                    except SourceError:
                        product = None
            if not product:
                product = {
                    "asin": item.get("asin"),
                    "marketplace": marketplace,
                    "title": item.get("title") or item.get("name") or "Untitled audiobook",
                    "release_date": item.get("release_date"),
                    "image_url": item.get("image_url", ""),
                    "audible_url": item.get("audible_url", ""),
                    "authors": item.get("authors", []),
                    "series": item.get("series", []),
                    "source_payload": item.get("payload") or item,
                }
            if self.settings.audnexus_enabled:
                try:
                    enriched = await self.audnexus.get_book(str(product["asin"]), marketplace)
                    if enriched:
                        merged = normalize_audnexus_book(enriched, marketplace)
                        product = enrich_missing_fields(product, merged)
                except SourceError:
                    pass
            # Keep user-entered fields when an upstream detail response is
            # sparse (which is common for future or region-limited ASINs).
            for key in ("title", "release_date", "image_url", "description", "authors", "series"):
                if product.get(key) in (None, "", []) and item.get(key) not in (None, "", []):
                    product[key] = item[key]

            # A series book is represented by its series follow. Only a true
            # standalone remains directly followed as a book.
            author_records = [
                author
                for author in product.get("authors", [])
                if is_followable_author(author) and author.get("asin")
            ]
            selected_author_asins = {
                str(asin).strip()
                for asin in item.get("follow_author_asins") or []
                if str(asin).strip()
            }
            scoped_author_asins = {
                str(asin).strip()
                for asin in item.get("author_scope_asins") or []
                if str(asin).strip()
            }
            selected_authors = [
                author for author in author_records
                if str(author.get("asin")) in selected_author_asins
                and str(author.get("asin")) not in scoped_author_asins
            ]
            series_records = [series for series in product.get("series", []) if series.get("asin") and series.get("name")]
            for series in series_records:
                upsert_series(self.db, series, marketplace, tracked=not scoped_author_asins)
            # A book follow never expands to credited authors implicitly.
            # Authors selected in the confirmation prompt are explicit broad
            # author follows, so their future releases are intentional.
            for author in selected_authors:
                author_id = upsert_author(
                    self.db,
                    {**author, "follow_all": True},
                    marketplace,
                    tracked=True,
                )
                set_author_follow_all(self.db, author_id, True)
            entity_id = upsert_book(self.db, product, marketplace, tracked=not scoped_author_asins)
            for author in author_records:
                if str(author.get("asin")) not in scoped_author_asins:
                    continue
                author_id = upsert_author(
                    self.db,
                    {**author, "follow_all": False},
                    marketplace,
                    tracked=True,
                )
                if series_records:
                    for series in series_records:
                        series_id = upsert_series(self.db, series, marketplace, tracked=False)
                        set_author_series_preference(
                            self.db,
                            author_id,
                            series_id,
                            True,
                            preserve_follow_all=True,
                        )
                else:
                    set_author_book_preference(
                        self.db,
                        author_id,
                        entity_id,
                        True,
                        preserve_follow_all=True,
                    )
            return {"kind": kind, "id": entity_id, "asin": product["asin"]}
        raise ValueError("Unknown watch kind")

    @source_operation("follow")
    async def add_author_group_watch(self, members: list[dict[str, Any]]) -> dict[str, Any]:
        """Follow each displayed author identity through one user operation."""

        targets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for member in members:
            if member.get("kind") not in (None, "author"):
                raise ValueError("Author groups may contain only author identities")
            asin = str(member.get("asin") or "").strip()
            name = str(member.get("name") or "").strip()
            if not asin or not name:
                raise ValueError("Every author identity requires an ASIN and name")
            if asin in seen:
                continue
            seen.add(asin)
            targets.append({**member, "kind": "author", "asin": asin, "name": name})
        if not targets:
            raise ValueError("At least one author identity is required")

        # Keep the existing author-follow path as the source of truth for
        # identity resolution and catalogue hydration. The browser still
        # makes one request and receives one result for the whole group.
        results = []
        for target in targets:
            result = await self.add_watch("author", target)
            # A name-only identity can resolve to an existing canonical row,
            # so retain the requested source ASIN for client-side mapping as
            # well as the persisted canonical ASIN.
            results.append({"requested_asin": target["asin"], **result})
        return {"kind": "author-group", "members": results}

    async def set_author_series(self, item: dict[str, Any]) -> dict[str, Any]:
        """Select the series a followed author should contribute to the calendar.

        The operation is local-only: books already returned by the author
        details modal are persisted so the calendar can update immediately;
        the next normal sync will fill in anything not present in that view.
        """

        action = str(item.get("action") or "select").casefold()
        if action not in {"select", "deselect", "all", "none"}:
            raise ValueError("Unknown author series action")
        release_kind = str(item.get("release_kind") or "series").casefold()
        if release_kind not in {"series", "book"}:
            raise ValueError("Unknown author release kind")
        author_asin = str(item.get("author_asin") or "").strip()
        author_name = str(item.get("author_name") or "").strip()
        if not author_asin or not author_name:
            raise ValueError("Author ASIN and name are required")
        if not is_followable_author({"asin": author_asin, "name": author_name}):
            raise ValueError("Contributor records cannot be followed")
        marketplace = item.get("marketplace") or self.settings.region
        if marketplace != self.settings.region and not (self.settings.demo_data and marketplace == "demo"):
            raise ValueError("This item belongs to another region; refresh the page before following it")

        existing_author = next((author for author in list_authors(self.db)
                                if author["marketplace"] == marketplace and author_asin in source_asins(author)), None)
        if action in {"deselect", "all", "none"} and not existing_author:
            return {"status": "ok", "action": action, "author_asin": author_asin}
        author_id = upsert_author(
            self.db,
            {
                "asin": author_asin,
                "name": author_name,
                "follow_all": action == "all",
                "payload": item.get("author") or {"asin": author_asin, "name": author_name},
            },
            marketplace,
            tracked=True,
        )
        author_is_broad = bool(existing_author and existing_author.get("follow_all"))
        if action == "all":
            set_author_follow_all(self.db, author_id, True)
            return {"status": "ok", "action": action, "author_asin": author_asin, "series_filter_active": False}

        if action == "none":
            clear_author_release_selections(self.db, author_id)
            return {
                "status": "ok",
                "action": action,
                "author_asin": author_asin,
                "series_filter_active": True,
                "author_follow_all": False,
            }

        if release_kind == "book":
            book_asin = str(item.get("book_asin") or "").strip()
            if not book_asin:
                raise ValueError("Book ASIN is required")
            book = dict(item.get("book") or {})
            book["asin"] = book_asin
            authors = [author for author in book.get("authors") or [] if isinstance(author, dict)]
            if not any(
                author_candidate_matches(person, {"asin": author_asin, "name": author_name})
                for person in authors
            ):
                authors.append({"asin": author_asin, "name": author_name})
            book["authors"] = authors
            book_id = upsert_book(self.db, book, marketplace)
            set_author_book_preference(
                self.db,
                author_id,
                book_id,
                action == "select",
                preserve_follow_all=author_is_broad,
            )
            return {
                "status": "ok",
                "action": action,
                "author_asin": author_asin,
                "book_asin": book_asin,
                "series_filter_active": True,
            }

        series_asin = str(item.get("series_asin") or "").strip()
        series_name = str(item.get("series_name") or "").strip()
        if not series_asin or not series_name:
            raise ValueError("Series ASIN and name are required")
        series_id = upsert_series(
            self.db,
            {
                "asin": series_asin,
                "name": series_name,
                "payload": item.get("series") or {"asin": series_asin, "name": series_name},
            },
            marketplace,
            # An author-modal selection is scoped to that author; it must not
            # create a separate direct series follow.
            tracked=False,
        )
        if action == "deselect":
            # This is an author-scoped selection, not a direct series follow.
            # The series may not yet be linked to the author locally, so write
            # the preference directly instead of routing through global
            # unfollow validation.
            set_author_series_preference(
                self.db,
                author_id,
                series_id,
                False,
                preserve_follow_all=author_is_broad,
            )
            return {
                "status": "ok",
                "action": action,
                "author_asin": author_asin,
                "series_asin": series_asin,
                "series_filter_active": True,
            }
        set_author_series_preference(
            self.db,
            author_id,
            series_id,
            True,
            preserve_follow_all=True,
        )

        if action == "select":
            author_record = {"asin": author_asin, "name": author_name}
            for raw_book in item.get("books") or []:
                if not isinstance(raw_book, dict) or not raw_book.get("asin"):
                    continue
                book = dict(raw_book)
                authors = [author for author in book.get("authors") or [] if isinstance(author, dict)]
                if not any(
                    author_candidate_matches(person, author_record)
                    for person in authors
                ) and (
                    not authors
                    or not any(
                        person.get("asin")
                        and not str(person.get("asin")).startswith("name:")
                        for person in authors
                    )
                ):
                    authors.append(author_record)
                series_items = [series for series in book.get("series") or [] if isinstance(series, dict)]
                target_series = {"asin": series_asin, "name": series_name}
                if not any(series_identity_matches(series, target_series) for series in series_items):
                    if any(
                        series.get("asin")
                        and not str(series.get("asin")).startswith("name:")
                        for series in series_items
                    ):
                        continue
                    series_items.append({"asin": series_asin, "name": series_name})
                book["authors"] = authors
                book["series"] = series_items
                upsert_book(self.db, book, marketplace)

        return {
            "status": "ok",
            "action": action,
            "author_asin": author_asin,
            "series_asin": series_asin,
            "series_filter_active": True,
        }
