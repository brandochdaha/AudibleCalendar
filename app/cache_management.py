"""Explicit administrator requests to clear cached responses and artwork."""

import re

from .db import Database
from .storage import cache_databases, measure_cache_usage


def clear_cache(manager, *, clear_responses: bool, clear_artwork: bool) -> dict:
    """Run with API admissions paused and all workspaces idle."""
    databases, regional_paths = cache_databases(manager)
    responses_removed = 0
    artwork_removed = 0
    for path in databases:
        with Database(path).transaction() as connection:
            if clear_responses:
                responses_removed += connection.execute("SELECT COUNT(*) FROM source_cache").fetchone()[0]
                connection.execute("DELETE FROM source_cache")
            if clear_artwork:
                connection.execute("DELETE FROM image_cache")
        Database(path).compact()

    if clear_artwork:
        directories = [manager.artwork_directory, *(path.parent / "images" for path in regional_paths)]
        for directory in directories:
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in directory.iterdir():
                if (not path.is_symlink() and path.is_file()
                        and re.fullmatch(r"[a-f0-9]{64}(?:\.(?:jpg|png|webp|gif|avif))?", path.name)):
                    path.unlink(missing_ok=True)
                    artwork_removed += 1
    if clear_responses:
        manager.source_cache.failures.clear()
    return {
        "responses_removed": responses_removed,
        "artwork_removed": artwork_removed,
        "storage_usage": measure_cache_usage(manager),
    }
