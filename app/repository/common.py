"""Repository common: local SQLite operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from .presentation import summary_mode, INTERNAL_FIELDS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(row: Any) -> dict[str, Any]:
    result = {key: value for key, value in dict(row).items() if not summary_mode.get() or key not in INTERNAL_FIELDS}
    for key in ("source_payload",):
        if result.get(key):
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                pass
    for key in (
        "is_tracked",
        "follow_all",
        "is_favourite",
        "is_verified",
        "is_preorder",
        "is_selected",
    ):
        if key in result:
            result[key] = bool(result[key])
    if "favourite_override" in result and result["favourite_override"] is not None:
        result["favourite_override"] = bool(result["favourite_override"])
    return result


def _series_favourite_sql(alias: str = "s") -> str:
    """Return the effective favourite expression for a series row."""

    return f"""
        CASE WHEN {_series_followed_sql(alias)}
             THEN CASE WHEN {alias}.favourite_override IS NOT NULL
                       THEN {alias}.favourite_override
                       ELSE EXISTS (
                 SELECT 1
                 FROM authors favourite_author
                 WHERE favourite_author.is_tracked = 1
                   AND favourite_author.is_favourite = 1
                   AND (
                       (
                           favourite_author.follow_all = 1
                           AND EXISTS (
                               SELECT 1
                               FROM book_authors favourite_ba
                               JOIN book_series favourite_bs
                                 ON favourite_bs.book_id = favourite_ba.book_id
                               WHERE favourite_ba.author_id = favourite_author.id
                                 AND favourite_bs.series_id = {alias}.id
                           )
                           AND NOT EXISTS (
                               SELECT 1
                               FROM author_series_preferences excluded_asp
                               WHERE excluded_asp.author_id = favourite_author.id
                                 AND excluded_asp.series_id = {alias}.id
                                 AND excluded_asp.is_selected = 0
                           )
                       )
                       OR EXISTS (
                           SELECT 1
                           FROM author_series_preferences favourite_asp
                           WHERE favourite_asp.author_id = favourite_author.id
                             AND favourite_asp.series_id = {alias}.id
                             AND favourite_asp.is_selected = 1
                       )
                   )
                       )
                  END
             ELSE 0
        END
    """


def _author_series_scope_sql(author_alias: str = "a", series_id_expr: str = "s.id") -> str:
    """Return the SQL predicate for a series followed through an author.

    A broad author follow includes every known series unless a zero-valued
    preference explicitly excludes that series. Positive preferences remain
    the legacy explicit-series whitelist used when ``follow_all`` is false.
    """

    return f"""
        (
            (
                {author_alias}.follow_all = 1
                AND NOT EXISTS (
                    SELECT 1
                    FROM author_series_preferences excluded_asp
                    WHERE excluded_asp.author_id = {author_alias}.id
                      AND excluded_asp.series_id = {series_id_expr}
                      AND excluded_asp.is_selected = 0
                )
            )
            OR EXISTS (
                SELECT 1
                FROM author_series_preferences selected_asp
                WHERE selected_asp.author_id = {author_alias}.id
                  AND selected_asp.series_id = {series_id_expr}
                  AND selected_asp.is_selected = 1
            )
        )
    """


def _author_book_scope_sql(author_alias: str = "a", book_id_expr: str = "b.id") -> str:
    """Return the SQL predicate for a book followed through an author."""

    return f"""
        (
            EXISTS (
                SELECT 1
                FROM book_series scoped_bs
                WHERE scoped_bs.book_id = {book_id_expr}
                  AND {_author_series_scope_sql(author_alias, "scoped_bs.series_id")}
            )
            OR (
                NOT EXISTS (
                    SELECT 1 FROM book_series standalone_bs
                    WHERE standalone_bs.book_id = {book_id_expr}
                )
                AND (
                    (
                        {author_alias}.follow_all = 1
                        AND NOT EXISTS (
                            SELECT 1
                            FROM author_book_preferences excluded_abp
                            WHERE excluded_abp.author_id = {author_alias}.id
                              AND excluded_abp.book_id = {book_id_expr}
                              AND excluded_abp.is_selected = 0
                        )
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM author_book_preferences selected_abp
                        WHERE selected_abp.author_id = {author_alias}.id
                          AND selected_abp.book_id = {book_id_expr}
                          AND selected_abp.is_selected = 1
                    )
                )
            )
        )
    """


def _series_followed_sql(series_alias: str = "s") -> str:
    """Return the predicate for a series followed by any effective source."""

    return f"""(
        {series_alias}.is_tracked = 1
        OR EXISTS (
            SELECT 1
            FROM book_series followed_bs
            JOIN book_authors followed_ba ON followed_ba.book_id = followed_bs.book_id
            JOIN authors followed_author ON followed_author.id = followed_ba.author_id
            WHERE followed_bs.series_id = {series_alias}.id
              AND followed_author.is_tracked = 1
              AND {_author_series_scope_sql("followed_author", f"{series_alias}.id")}
        )
        OR EXISTS (
            SELECT 1
            FROM author_series_preferences followed_asp
            JOIN authors followed_author ON followed_author.id = followed_asp.author_id
            WHERE followed_asp.series_id = {series_alias}.id
              AND followed_asp.is_selected = 1
              AND followed_author.is_tracked = 1
        )
    )"""


def _followed_book_scope_sql(book_alias: str = "b") -> str:
    """Match direct book follows and effective author/series follows."""
    return f"""(
        {book_alias}.is_tracked = 1
        OR EXISTS (
            SELECT 1 FROM book_authors followed_ba
            JOIN authors followed_author ON followed_author.id = followed_ba.author_id
            WHERE followed_ba.book_id = {book_alias}.id AND followed_author.is_tracked = 1
              AND {_author_book_scope_sql("followed_author", f"{book_alias}.id")}
        )
        OR EXISTS (
            SELECT 1 FROM book_series followed_bs
            JOIN series followed_series ON followed_series.id = followed_bs.series_id
            WHERE followed_bs.book_id = {book_alias}.id AND followed_series.is_tracked = 1
        )
    )"""
