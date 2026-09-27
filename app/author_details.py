"""Author profile and catalogue assembly for the details modal."""

from __future__ import annotations

from typing import Any

from .instrumentation import record_pagination
from .book_view import book_summary
from .catalogue_snapshot import CatalogueSnapshot
from .author_sources import author_search_names, source_asins
from .author_discovery import resolve_catalogue_author
from .sync_matching import author_candidate_matches
from .author_identity import author_display_names, author_identities_can_merge
from .config import Settings
from .db import Database
from .normalization import extract_products, infer_missing_series, normalize_products, series_sequence_from_title
from .repository import (
    get_book,
    list_author_book_preferences,
    list_author_series_preferences,
    list_authors,
)
from .sources.audible import SourceError
from .repository.presentation import summaries


class AuthorDetailsBuilder:
    def __init__(self, db: Database, settings: Settings, audible: Any, audnexus: Any):
        self.db = db
        self.settings = settings
        self.audible = audible
        self.audnexus = audnexus

    @staticmethod
    def _author_matches(candidate: dict[str, Any], asin: str, name: str) -> bool:
        if asin and asin in source_asins(candidate):
            return True
        return bool(
            name
            and author_identities_can_merge(
                {"asin": asin, "name": name},
                candidate,
            )
        )

    @staticmethod
    def _author_description(payload: dict[str, Any]) -> str | None:
        for key in ("description", "bio", "blurb"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip() or None
        return None

    @staticmethod
    def _author_image(payload: dict[str, Any]) -> str:
        """Read the contributor image aliases used by Audible/Audnexus."""

        value = payload.get("image_url") or payload.get("image")
        if isinstance(value, dict):
            value = value.get("url") or value.get("image_url") or value.get("large") or value.get("small")
        if isinstance(value, (list, tuple)):
            value = next((item for item in value if isinstance(item, str) and item.strip()), "")
        return str(value).strip() if value not in (None, "") else ""

    @staticmethod
    def _public_details(payload: dict[str, Any]) -> list[dict[str, str]]:
        """Turn optional source metadata into safe, readable label/value pairs."""

        hidden = {"asin", "id", "name", "description", "bio", "blurb", "image", "image_url", "books", "similar"}
        details: list[dict[str, str]] = []
        for key, value in payload.items():
            if key.casefold() in hidden or value in (None, "", [], {}):
                continue
            if isinstance(value, list):
                values = []
                for item in value:
                    if isinstance(item, dict):
                        label = item.get("name") or item.get("title") or item.get("value")
                        if label:
                            values.append(str(label))
                    elif isinstance(item, (str, int, float, bool)):
                        values.append(str(item))
                display = ", ".join(values)
            elif isinstance(value, dict):
                display = ", ".join(f"{item_key}: {item_value}" for item_key, item_value in value.items())
            else:
                display = str(value)
            if display:
                label = key.replace("_", " ").strip().title()
                details.append({"label": label, "value": display})
        return details

    async def build(self, asin: str, name: str) -> dict[str, Any]:
        """Build an author profile and grouped catalogue from public sources."""

        asin = asin.strip()
        name = name.strip()
        if not asin or not name:
            raise ValueError("Author ASIN and name are required")

        author: dict[str, Any] = {
            "asin": asin,
            "name": name,
            "description": None,
            "image_url": "",
            "source": "audible",
        }
        metadata_payload: dict[str, Any] = {}

        local_author: dict[str, Any] | None = None
        for local in list_authors(self.db):
            if local.get("marketplace") == self.settings.region and self._author_matches(local, asin, name):
                local_author = local
                author.update(
                    {
                        "name": local.get("name") or author["name"],
                        "description": local.get("description"),
                        "image_url": local.get("image_url") or "",
                    }
                )
                payload = local.get("source_payload")
                if isinstance(payload, dict):
                    metadata_payload.update(payload)
                    author["description"] = author.get("description") or self._author_description(payload)
                    author["image_url"] = author.get("image_url") or self._author_image(payload)
                break

        preferences = list_author_series_preferences(self.db, local_author["id"]) if local_author else []
        book_preferences = list_author_book_preferences(self.db, local_author["id"]) if local_author else []
        author_followed = bool(local_author and local_author.get("is_tracked"))
        follow_all = bool(author_followed and local_author.get("follow_all"))
        selected_series_asins = {
            str(item.get("series_asin"))
            for item in preferences
            if item.get("is_selected") and item.get("series_asin")
        }
        excluded_series_asins = {
            str(item.get("series_asin"))
            for item in preferences
            if not item.get("is_selected") and item.get("series_asin")
        }
        selected_book_asins = {
            str(item.get("book_asin"))
            for item in book_preferences
            if item.get("is_selected") and item.get("book_asin")
        }
        excluded_book_asins = {
            str(item.get("book_asin"))
            for item in book_preferences
            if not item.get("is_selected") and item.get("book_asin")
        }

        products: dict[str, dict[str, Any]] = {}
        source_error: str | None = None
        for search_name in author_search_names(local_author or author, name):
            try:
                for page in range(self.settings.sync_max_pages):
                    payload = await self.audible.search_products_page(
                        search_name, "author", page,
                        cache_ttl_seconds=self.settings.cache_author_hours * 3600,
                    )
                    page_products = normalize_products(payload, self.settings.region, english_only=self.settings.english_only)
                    if not extract_products(payload):
                        record_pagination(self, "empty")
                        break
                    identity = resolve_catalogue_author(self.db, local_author or author, page_products, self.settings.region)
                    if local_author:
                        local_author = identity
                    author.update({key: identity[key] for key in ("source_asins", "source_identities") if key in identity})
                    for product in page_products:
                        matched = next(
                            (person for person in product["authors"] if author_candidate_matches(person, identity)),
                            None,
                        )
                        if matched:
                            products[product["asin"]] = product
                            metadata_payload.update({key: value for key, value in matched.items() if value not in (None, "", [], {})})
                            if matched.get("name") and author["name"] == name:
                                author["name"] = matched["name"]
                            if self._author_description(matched):
                                author["description"] = self._author_description(matched)
                            if self._author_image(matched):
                                author["image_url"] = self._author_image(matched)
                    if len(extract_products(payload)) < self.settings.sync_page_size:
                        record_pagination(self, "short")
                        break
                else:
                    record_pagination(self, "limit")
                    source_error = source_error or f"Catalog results may be incomplete: reached the {page + 1}-page discovery limit"
            except SourceError as exc:
                source_error = source_error or str(exc)

        infer_missing_series(products.values(), author_identities=[local_author or author])
        for product in products.values():
            if product["series"] or series_sequence_from_title(product.get("title"))[1] is None:
                continue
            local_book = get_book(self.db, product["asin"], self.settings.region)
            if local_book:
                product["series"] = book_summary(local_book)["series"]
        unresolved = [product for product in products.values()
                      if not product["series"]
                      and series_sequence_from_title(product.get("title"))[1] is not None]
        if unresolved:
            with summaries():
                known = CatalogueSnapshot.load(self.db).books
            infer_missing_series(unresolved, author_identities=[local_author or author],
                                 known_products=[book for book in known
                                                 if book.get("marketplace") == self.settings.region])
        groups: dict[str, dict[str, Any]] = {}
        for product in products.values():
            series_items = product["series"] or [{"asin": "__standalone__", "name": "Standalone"}]
            for series in series_items:
                series_key = str(series.get("asin") or series.get("name") or "__standalone__")
                group = groups.setdefault(
                    series_key,
                    {
                        "asin": None if series_key == "__standalone__" else series.get("asin"),
                        "name": series.get("name") or "Standalone",
                        "books": [],
                    },
                )
                if any(book["asin"] == product["asin"] for book in group["books"]):
                    continue
                book = book_summary(product)
                book["series_sequence"] = series.get("sequence")
                book["series_sequence_number"] = series.get("sequence_number")
                book["is_selected"] = bool(
                    author_followed
                    and (
                        (
                            follow_all
                            and (
                                series_key == "__standalone__"
                                and str(product.get("asin") or "") not in excluded_book_asins
                                or series_key != "__standalone__"
                                and series_key not in excluded_series_asins
                            )
                        )
                        or (
                            series_key == "__standalone__"
                            and str(product.get("asin") or "") in selected_book_asins
                        )
                        or (
                            series_key != "__standalone__"
                            and str(series.get("asin") or "") in selected_series_asins
                        )
                    )
                )
                group["books"].append(book)

        grouped = list(groups.values())
        for group in grouped:
            group["is_selected"] = bool(
                author_followed
                and group.get("asin")
                and (
                    (follow_all and str(group["asin"]) not in excluded_series_asins)
                    or str(group["asin"]) in selected_series_asins
                )
            )
            group["books"].sort(
                key=lambda book: (
                    book.get("release_date") is None,
                    book.get("release_date") or "",
                    book["title"].casefold(),
                )
            )
        grouped.sort(key=lambda group: (group["name"] != "Standalone", group["name"].casefold()))

        result = {
            "kind": "author",
            "author": {
                key: author[key]
                for key in ("asin", "name", "description", "image_url", "source")
            },
            "author_followed": author_followed,
            "author_follow_all": follow_all,
            "series_filter_active": bool(
                author_followed and (not follow_all or preferences or book_preferences)
            ),
            "metadata": self._public_details(metadata_payload),
            "series": grouped,
            "book_count": len(products),
        }
        if source_error:
            result["error"] = source_error
        return result

    @staticmethod
    def _group_member(member: dict[str, Any]) -> dict[str, Any]:
        result = dict(member)
        for key in ("author_group_members", "author_group_aliases", "author_group_evidence"):
            result.pop(key, None)
        return result

    @staticmethod
    def _group_source_identities(
        members: list[dict[str, Any]],
        results: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        identities: dict[str, dict[str, str]] = {}
        authors = [*members, *(result.get("author") or {} for result in results)]
        for member in authors:
            sources = member.get("source_identities") or []
            if member.get("asin"):
                sources = [{"asin": member["asin"], "name": member.get("name")}, *sources]
            for source in sources:
                if not isinstance(source, dict) or not source.get("asin"):
                    continue
                asin = str(source["asin"])
                identities.setdefault(asin, {
                    "asin": asin,
                    "name": str(source.get("name") or member.get("name") or ""),
                })
        return list(identities.values())

    @staticmethod
    def _merge_group_series(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for result in results:
            for raw_group in result.get("series") or []:
                if not isinstance(raw_group, dict):
                    continue
                asin = str(raw_group.get("asin") or "").strip()
                name = str(raw_group.get("name") or "Standalone").strip()
                key = f"asin:{asin}" if asin else f"name:{name.casefold()}"
                group = groups.setdefault(key, {"asin": raw_group.get("asin"), "name": name, "books": []})
                group["is_selected"] = bool(group.get("is_selected") or raw_group.get("is_selected"))
                books_by_asin = {str(book.get("asin")): book for book in group["books"] if book.get("asin")}
                for raw_book in raw_group.get("books") or []:
                    if not isinstance(raw_book, dict) or not raw_book.get("asin"):
                        continue
                    asin_key = str(raw_book["asin"])
                    existing = books_by_asin.get(asin_key)
                    if existing is None:
                        existing = dict(raw_book)
                        group["books"].append(existing)
                        books_by_asin[asin_key] = existing
                    else:
                        existing["is_selected"] = bool(existing.get("is_selected") or raw_book.get("is_selected"))
                        for field, value in raw_book.items():
                            if existing.get(field) in (None, "", [], {}) and value not in (None, "", [], {}):
                                existing[field] = value
                group["books"].sort(
                    key=lambda book: (
                        book.get("release_date") is None,
                        book.get("release_date") or "",
                        str(book.get("title") or "").casefold(),
                    )
                )
        return sorted(
            groups.values(),
            key=lambda group: (group.get("name") != "Standalone", str(group.get("name") or "").casefold()),
        )

    async def build_group(self, members: list[dict[str, Any]], *, loader=None) -> dict[str, Any]:
        """Build one combined catalogue from several presentation-level identities."""

        loader = loader or self.build
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for member in members:
            if not isinstance(member, dict):
                continue
            asin = str(member.get("asin") or "").strip()
            name = str(member.get("name") or "").strip()
            if not asin or not name or asin in seen:
                continue
            seen.add(asin)
            unique.append({**member, "asin": asin, "name": name})
        if not unique:
            raise ValueError("At least one author identity is required")
        if len(unique) == 1:
            return await loader(unique[0]["asin"], unique[0]["name"])

        results = [await loader(member["asin"], member["name"]) for member in unique]
        primary = dict(results[0].get("author") or unique[0])
        names = [str(member.get("name") or "") for member in unique]
        names.extend(
            str(source.get("name") or "")
            for member in unique
            for source in member.get("source_identities") or []
        )
        display_names = author_display_names(names)
        primary_name = str(primary.get("name") or unique[0]["name"])
        aliases = [name for name in display_names if name.casefold() != primary_name.casefold()]
        source_identities = self._group_source_identities(unique, results)
        primary.update({
            "asin": primary.get("asin") or unique[0]["asin"],
            "name": primary_name,
            "source_asins": [source["asin"] for source in source_identities],
            "source_identities": source_identities,
            "author_group": True,
            "author_group_members": [self._group_member(member) for member in unique],
            "author_group_member_count": len(unique),
            "author_group_aliases": aliases,
        })
        for result in results:
            result_author = result.get("author") or {}
            for key in ("description", "image_url"):
                if primary.get(key) in (None, "") and result_author.get(key) not in (None, ""):
                    primary[key] = result_author[key]

        metadata: list[dict[str, str]] = []
        seen_metadata: set[tuple[str, str]] = set()
        for result in results:
            for item in result.get("metadata") or []:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("label") or ""), str(item.get("value") or ""))
                if key[0] and key[1] and key not in seen_metadata:
                    seen_metadata.add(key)
                    metadata.append({"label": key[0], "value": key[1]})

        errors = []
        for result in results:
            error = str(result.get("error") or "").strip()
            if error and error not in errors:
                errors.append(error)
        combined = {
            "kind": "author",
            "author": primary,
            "author_group": True,
            "author_group_members": [self._group_member(member) for member in unique],
            "author_group_aliases": aliases,
            "author_group_member_count": len(unique),
            "author_followed": any(result.get("author_followed") for result in results),
            "author_follow_all": any(result.get("author_follow_all") for result in results),
            "series_filter_active": any(result.get("series_filter_active") for result in results),
            "metadata": metadata,
            "series": self._merge_group_series(results),
        }
        combined["book_count"] = len({
            str(book.get("asin"))
            for group in combined["series"]
            for book in group.get("books") or []
            if book.get("asin")
        })
        if errors:
            combined["error"] = "; ".join(errors)
        return combined
