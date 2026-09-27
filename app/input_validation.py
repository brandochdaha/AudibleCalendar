"""Validation for values crossing catalogue and archive trust boundaries."""

import re
from datetime import date


def release_date(value: str | None) -> str | None:
    if value in (None, ""):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Release dates must use YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def validate_catalogue_dates(value) -> None:
    """Nested follow payloads must meet the same contract as book rows."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "release_date":
                release_date(item)
            elif isinstance(item, (dict, list)):
                validate_catalogue_dates(item)
    elif isinstance(value, list):
        for item in value:
            validate_catalogue_dates(item)
