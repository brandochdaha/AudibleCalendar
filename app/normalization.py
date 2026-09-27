"""Normalise the slightly different Audible and Audnexus response shapes."""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Iterable


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value)).strip()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_NON_FOLLOWABLE_AUTHOR_SUFFIX = re.compile(
    r"\s*-\s*(?:translator|editor|artist|contributor)\s*$",
    re.IGNORECASE,
)


def is_followable_author(person: Any) -> bool:
    """Return whether a contributor record may participate in user follows.

    Audible sometimes exposes translators, editors, artists, and similar
    credits as author-shaped records whose names carry a role suffix. Keep
    those records in the raw catalogue payload, but never turn them into a
    follow relationship.
    """

    if isinstance(person, Mapping):
        name = str(person.get("name") or "").strip()
    else:
        try:
            name = str(person["name"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return False
    return bool(name) and not _NON_FOLLOWABLE_AUTHOR_SUFFIX.search(name)


def _image_url(value: Any) -> str | None:
    """Extract a usable URL from the image shapes returned by catalogue sources."""

    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("url", "image_url", "large", "medium", "small"):
            candidate = _image_url(value.get(key))
            if candidate:
                return candidate
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _image_url(item)
            if candidate:
                return candidate
    return None


def _stable_name_id(name: str) -> str:
    """Create a deterministic local identity when Audible omits a contributor ASIN."""

    slug = re.sub(r"[^\w]+", "-", unicodedata.normalize("NFKC", name).casefold(), flags=re.UNICODE).strip("-")
    return f"name:{slug or 'unknown'}"


_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_NUMBER_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_WRITTEN_NUMBERS = {word: number for number, word in enumerate(_NUMBER_WORDS)}
for _tens_number, _tens_word in enumerate(_NUMBER_TENS, start=2):
    _WRITTEN_NUMBERS[_tens_word] = _tens_number * 10
    for _unit_number, _unit_word in enumerate(_NUMBER_WORDS[1:10], start=1):
        _WRITTEN_NUMBERS[f"{_tens_word} {_unit_word}"] = _tens_number * 10 + _unit_number

_SERIES_VOLUME_SUFFIX = re.compile(
    r"(?:[,;:\-]\s*|\s+)(?:vol(?:ume)?|book|part|#)\s*\.?\s*"
    r"(\d+(?:\.\d+)?|[a-z]+(?:[ -][a-z]+)?)\s*$",
    re.IGNORECASE,
)
_SERIES_NUMBER_SUFFIX = re.compile(r"(?:[,;:\-]\s*|\s+)(\d+(?:\.\d+)?)\s*$")


def _series_name_key(value: Any) -> str:
    """Normalize a complete name without discarding numbers in that name."""

    text = unicodedata.normalize("NFKC", _clean_text(value) or "").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)).strip()


def series_title_key(value: Any) -> str:
    """Normalize a series/title name for conservative relationship inference."""

    text = _clean_text(value) or ""
    text = unicodedata.normalize("NFKC", text).casefold()
    match = _SERIES_VOLUME_SUFFIX.search(text)
    if match and _series_number(match.group(1)) is not None:
        text = text[:match.start()]
    # Identity callers use this key too: a bare number can belong to the name.
    return _series_name_key(text)


def series_identity_matches(left: Any, right: Any) -> bool:
    """Match a series by ASIN; use names only when neither side has one."""

    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    left_asin = str(left.get("asin") or "").strip()
    right_asin = str(right.get("asin") or "").strip()
    left_has_identity = bool(left_asin and not left_asin.startswith("name:"))
    right_has_identity = bool(right_asin and not right_asin.startswith("name:"))
    if left_has_identity or right_has_identity:
        return bool(left_asin and right_asin and left_asin == right_asin)
    if left_asin or right_asin:
        return bool(left_asin and right_asin and left_asin == right_asin)
    return bool(
        series_title_key(left.get("name") or left.get("title"))
        and series_title_key(left.get("name") or left.get("title"))
        == series_title_key(right.get("name") or right.get("title"))
    )


def explicit_author_asins(authors: Any) -> set[str]:
    """Return stable contributor identities suitable for conservative inference."""

    return {
        str(author.get("asin")).strip()
        for author in _as_list(authors)
        if isinstance(author, Mapping)
        and author.get("asin")
        and not str(author.get("asin")).strip().startswith("name:")
    }


def series_author_asins(
    authors: Any, author_identities: Iterable[Mapping[str, Any]] = (),
) -> set[str]:
    """Return recorded contributor identities and confirmed local aliases.

    A name-derived identity can match the exact credit on a known series
    member, even when its author row remains separate from a stable ASIN.
    Display names alone never supply evidence.
    """

    people = [person for person in _as_list(authors) if isinstance(person, Mapping)]
    credited_asins = {str(person.get("asin") or "").strip() for person in people} - {""}
    result = set(credited_asins)
    for author in author_identities:
        sources = [author.get("asin"), *(author.get("source_asins") or [])]
        sources.extend(
            source.get("asin") for source in (author.get("source_identities") or [])
            if isinstance(source, Mapping)
        )
        aliases = {str(value).strip() for value in sources if value}
        if credited_asins & aliases:
            result.update(asin for asin in aliases if not asin.startswith("name:"))
    return result


def person_name_key(value: Any) -> str:
    """Normalize a contributor name for identity reconciliation."""

    text = unicodedata.normalize("NFKC", _clean_text(value) or "").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)).strip()


def person_name_fingerprint(value: Any) -> str:
    """Return a compact name fingerprint for obvious formatting duplicates.

    Audible occasionally returns the same name with inconsistent spacing or a
    duplicated token (for example, ``X Karnation`` and ``XKarnation
    XKarnation``). This is intentionally a secondary signal: stable Audible
    identities are still never merged with one another by name alone.
    """

    tokens = person_name_key(value).split()
    if not tokens:
        return ""
    # Collapse an exact repeated phrase, not arbitrary repeated words inside a
    # legitimate name (for example, "John John Smith").
    for width in range(len(tokens) // 2, 0, -1):
        if len(tokens) % width:
            continue
        repeats = len(tokens) // width
        pattern = tokens[:width]
        if repeats > 1 and tokens == pattern * repeats:
            tokens = pattern
            break
    return "".join(tokens)


def _series_number(value: str) -> float | None:
    text = value.casefold().replace("-", " ")
    if text in _WRITTEN_NUMBERS:
        return float(_WRITTEN_NUMBERS[text])
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    # Accept canonical Roman numerals only, avoiding arbitrary letter suffixes.
    roman = r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
    if not text or not re.fullmatch(roman, text):
        return None
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    for index, char in enumerate(text):
        subtract = index + 1 < len(text) and values[char] < values[text[index + 1]]
        total += -values[char] if subtract else values[char]
    return float(total)


def _series_sequence_match(text: str) -> re.Match[str] | None:
    match = _SERIES_VOLUME_SUFFIX.search(text) or _SERIES_NUMBER_SUFFIX.search(text)
    return match if match and _series_number(match.group(1)) is not None else None


def series_sequence_from_title(title: Any) -> tuple[str | None, float | None]:
    """Extract a trailing volume/book/part number or plain numeric suffix."""

    text = _clean_text(title) or ""
    match = _series_sequence_match(text)
    if not match:
        return None, None
    return match.group(1), _series_number(match.group(1))


def infer_series_item_from_title(title: Any, series: dict[str, Any]) -> dict[str, Any] | None:
    """Return a series relation when a title matches a known series exactly.

    Matching ignores case and punctuation. Removing one trailing book/volume
    marker or plain number must leave exactly the complete known series name,
    including any numbers that belong to that name.
    """

    series_name = _clean_text(series.get("name"))
    text = _clean_text(title) or ""
    match = _series_sequence_match(text)
    if not series_name or not match:
        return None
    if _series_name_key(text[:match.start()]) != _series_name_key(series_name):
        return None
    return {
        "asin": str(series.get("asin") or _stable_name_id(series_name)),
        "name": series_name,
        "sequence": match.group(1),
        "sequence_number": _series_number(match.group(1)),
    }


def infer_missing_series(
    products: Iterable[dict[str, Any]],
    *,
    author_identities: Iterable[Mapping[str, Any]] = (),
    known_products: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Fill missing relations using repeated series and confirmed author evidence.

    Previously cached books can supply evidence without being changed or added
    to the returned products, which keeps recent-release discovery bounded.
    """

    product_list = list(products)
    identities = list(author_identities)
    known: dict[str, list[dict[str, Any]]] = {}
    for product in [*product_list, *known_products]:
        for series in product.get("series", []):
            key = _series_name_key(series.get("name"))
            if not key:
                continue
            candidate = next(
                (
                    item
                    for item in known.setdefault(key, [])
                    if series_identity_matches(item["series"], series)
                ),
                None,
            )
            if candidate is None:
                candidate = {"series": dict(series), "members": set(), "author_asins": set()}
                known[key].append(candidate)
            candidate["members"].add(str(product.get("asin") or id(product)))
            candidate["author_asins"].update(series_author_asins(product.get("authors"), identities))

    for product in product_list:
        if product.get("series"):
            continue
        title = _clean_text(product.get("title")) or ""
        match = _series_sequence_match(title)
        if not match:
            continue
        candidates = [
            candidate
            for candidate in known.get(_series_name_key(title[:match.start()]), [])
            if len(candidate["members"]) >= 2
            and infer_series_item_from_title(product.get("title"), candidate["series"])
        ]
        if candidates:
            author_asins = series_author_asins(product.get("authors"), identities)
            candidates = [
                candidate
                for candidate in candidates
                if author_asins & candidate["author_asins"]
            ]
        if len(candidates) == 1:
            product["series"] = [
                infer_series_item_from_title(product.get("title"), candidates[0]["series"])
            ]
    return product_list


def parse_date(value: Any) -> tuple[str | None, str]:
    if value is None:
        return None, "unknown"
    text = str(value).strip()
    if not text:
        return None, "unknown"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        precision = "day" if parsed.time().isoformat() != "00:00:00" else "day"
        return parsed.date().isoformat(), precision
    except ValueError:
        pass
    match = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text)
    if not match:
        return None, "unknown"
    year, month, day = match.groups()
    if not month:
        return year, "year"
    if not day:
        return f"{year}-{month}", "month"
    return f"{year}-{month}-{day}", "day"


def extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    # Audible uses ``similar_products`` for the response from the related
    # products endpoint, while catalogue search uses ``products``. Keep both
    # shapes (and the common aliases used by mocks/alternate sources) in the
    # same normalization path.
    for key in ("products", "similar_products", "items", "results", "books"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return [item for item in value if isinstance(item, dict)]
    product = payload.get("product")
    if isinstance(product, dict):
        return [product]
    if payload.get("asin"):
        return [payload]
    return []


def _person_list(product: dict[str, Any]) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    for value in (_as_list(product.get("authors")), _as_list(product.get("contributors"))):
        for item in value:
            if isinstance(item, str):
                people.append({"name": item})
            elif isinstance(item, dict):
                name = _clean_text(_first(item, "name", "author", "display_name"))
                if name:
                    asin = _first(item, "asin", "id")
                    person = {
                        "asin": str(asin or _stable_name_id(name)),
                        "name": name,
                        "role": _first(item, "role", "type"),
                    }
                    for detail_key in ("description", "bio", "blurb", "image", "image_url"):
                        detail_value = _first(item, detail_key)
                        if detail_value in (None, ""):
                            continue
                        # Audible contributor payloads have used both
                        # ``image`` and ``image_url`` over time.  Keep one
                        # canonical field so author images survive the
                        # normalisation and repository upsert paths.
                        if detail_key in {"image", "image_url"}:
                            image_url = _image_url(detail_value)
                            if image_url:
                                person["image_url"] = image_url
                        elif detail_key in {"description", "bio", "blurb"}:
                            # Store all source aliases in the canonical
                            # author description field used by the database
                            # and details modal.
                            person.setdefault("description", detail_value)
                        else:
                            person[detail_key] = detail_value
                    people.append(person)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for person in people:
        key = (str(person.get("asin") or ""), person["name"].casefold())
        existing = unique.get(key)
        if existing is None:
            unique[key] = person
            continue
        # Authors and contributors can repeat the same identity with
        # different fields populated. Merge the richer record instead of
        # allowing a sparse duplicate to discard an image or biography.
        for field, value in person.items():
            if value not in (None, "", [], {}):
                existing[field] = value
    return list(unique.values())


def _series_list(product: dict[str, Any]) -> list[dict[str, Any]]:
    values = product.get("series")
    if isinstance(values, dict):
        values = [values]
    series: list[dict[str, Any]] = []
    for item in _as_list(values):
        if isinstance(item, str):
            series.append({"name": item})
        elif isinstance(item, dict):
            name = _clean_text(_first(item, "name", "title"))
            if name:
                sequence = _first(item, "sequence", "sequence_number", "position")
                number = None
                try:
                    number = float(sequence) if sequence is not None else None
                except (TypeError, ValueError):
                    number = None
                series.append(
                    {
                        "asin": str(_first(item, "asin", "id") or _stable_name_id(name)),
                        "name": name,
                        "sequence": str(sequence) if sequence is not None else None,
                        "sequence_number": number,
                    }
                )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in series:
        unique[(str(item.get("asin") or ""), item["name"].casefold())] = item
    return list(unique.values())


def normalize_product(product: dict[str, Any], marketplace: str) -> dict[str, Any]:
    release_date, precision = parse_date(
        _first(product, "releaseDate", "release_date", "issueDate", "issue_date", "publicationDate")
    )
    images = product.get("product_images") or product.get("images") or {}
    image_url = _first(product, "image", "image_url", "cover_url")
    if not image_url and isinstance(images, dict):
        image_url = next((value for value in images.values() if isinstance(value, str)), None)
    runtime = _first(product, "runtimeLengthMin", "runtime_length_min", "runtimeMinutes")
    try:
        runtime = int(runtime) if runtime is not None else None
    except (TypeError, ValueError):
        runtime = None
    title = _clean_text(_first(product, "title", "name")) or "Untitled audiobook"
    asin = str(_first(product, "asin", "productId", "id") or "").strip()
    return {
        "asin": asin,
        "marketplace": marketplace,
        "title": title,
        "subtitle": _clean_text(_first(product, "subtitle")),
        # Audible's ``product_desc`` response group exposes the publisher
        # blurb as ``merchandising_summary`` on current public catalogue
        # responses. Keep the older aliases for alternate/API-version
        # payloads as well.
        "description": _clean_text(
            _first(product, "publisher_summary", "merchandising_summary", "summary", "description")
        ),
        "release_date": release_date,
        "release_date_precision": precision,
        "image_url": image_url or "",
        "audible_url": f"https://www.audible.{_domain_for_marketplace(marketplace)}/pd/{asin}" if asin else "",
        "publisher": _clean_text(_first(product, "publisherName", "publisher_name", "publisher")),
        "runtime_minutes": runtime,
        "language": _clean_text(_first(product, "language", "language_code")),
        "is_preorder": bool(_first(product, "isPreorder", "is_preorder", "preorder")),
        "is_available": _first(product, "isAvailable", "is_available"),
        "authors": _person_list(product),
        "series": _series_list(product),
        "source_payload": product,
    }


def is_english_language(value: Any) -> bool:
    """Return whether a catalogue language is English or unspecified."""

    if value in (None, ""):
        # Some local/demo records and sparse catalogue responses omit language.
        # Keep those records visible until a source provides a known language.
        return True
    language = str(value).strip().casefold().replace("_", "-")
    return language in {"en", "eng", "english"} or language.startswith("en-") or language.startswith("english ")


def normalize_products(payload: Any, marketplace: str, *, english_only: bool = False) -> list[dict[str, Any]]:
    products = [item for item in (normalize_product(raw, marketplace) for raw in extract_products(payload)) if item["asin"]]
    return [item for item in products if not english_only or is_english_language(item.get("language"))]


def normalize_audnexus_book(payload: dict[str, Any], marketplace: str) -> dict[str, Any]:
    return normalize_product(
        {
            **payload,
            "releaseDate": payload.get("releaseDate"),
            "image": payload.get("image"),
            "publisherName": payload.get("publisherName"),
            "runtimeLengthMin": payload.get("runtimeLengthMin"),
        },
        marketplace,
    )


def _domain_for_marketplace(marketplace: str) -> str:
    return {
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
    }.get(marketplace, "com")


def candidate_from_author(author: dict[str, Any], source: str = "audnexus") -> dict[str, Any]:
    name = _clean_text(_first(author, "name")) or "Unknown author"
    return {
        "kind": "author",
        "asin": str(_first(author, "asin", "id") or _stable_name_id(name)),
        "name": name,
        "image_url": _image_url(_first(author, "image", "image_url")) or "",
        "description": _clean_text(_first(author, "description", "bio", "blurb")),
        "source": source,
        "payload": author,
    }


def candidate_from_series(series: dict[str, Any], seed_asin: str | None = None, source: str = "audible") -> dict[str, Any]:
    name = _clean_text(_first(series, "name", "title")) or "Unnamed series"
    return {
        "kind": "series",
        "asin": str(_first(series, "asin", "id") or _stable_name_id(name)),
        "name": name,
        "seed_asin": seed_asin,
        "source": source,
        "payload": series,
    }
