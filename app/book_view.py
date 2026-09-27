"""Shared presentation fields for audiobook detail responses."""

from __future__ import annotations

from typing import Any


def book_summary(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "asin": product.get("asin"),
        "title": product.get("title") or "Untitled audiobook",
        "subtitle": product.get("subtitle"),
        "description": product.get("description"),
        "release_date": product.get("release_date"),
        "release_date_precision": product.get("release_date_precision"),
        "image_url": product.get("image_url", ""),
        "audible_url": product.get("audible_url", ""),
        "publisher": product.get("publisher"),
        "runtime_minutes": product.get("runtime_minutes"),
        "language": product.get("language"),
        "is_preorder": product.get("is_preorder", False),
        "is_available": product.get("is_available"),
        "authors": product.get("authors", []),
        "series": [
            {**series, "sequence": series.get("sequence") or series.get("sequence_text")}
            for series in product.get("series") or [] if isinstance(series, dict)
        ],
    }


def _runtime_label(minutes: Any) -> str | None:
    try:
        total_minutes = int(minutes) if minutes is not None else 0
    except (TypeError, ValueError):
        return None
    if total_minutes <= 0:
        return None
    hours, remainder = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if remainder or not parts:
        parts.append(f"{remainder} minute{'s' if remainder != 1 else ''}")
    return " ".join(parts)


def book_metadata(product: dict[str, Any]) -> list[dict[str, str]]:
    values = (
        ("Publisher", product.get("publisher")),
        ("Language", product.get("language")),
        ("Runtime", _runtime_label(product.get("runtime_minutes"))),
        ("Pre-order", "Yes" if product.get("is_preorder") else None),
        ("Availability", "Available" if product.get("is_available") else None),
    )
    return [{"label": label, "value": str(value)} for label, value in values if value not in (None, "")]
