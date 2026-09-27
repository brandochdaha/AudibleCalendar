"""Shared matching helpers for followed catalogue targets."""

from __future__ import annotations

from datetime import date
from typing import Any

from .author_identity import author_identities_can_merge


def product_release_date(product: dict[str, Any]) -> date | None:
    value = product.get("release_date")
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except (TypeError, ValueError):
        return None


def is_past_release(product: dict[str, Any], today: date | None = None) -> bool:
    release = product_release_date(product)
    return release is not None and release < (today or date.today())


def author_candidate_matches(candidate: dict[str, Any], author: dict[str, Any]) -> bool:
    """Only confirmed source identities can contribute to a followed author."""
    return author_identities_can_merge(candidate, author)
