"""Regional library paths and the one-time split of existing user databases."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from .api_models import SettingsUpdate
from .db import Database
from .settings_store import load_settings, save_settings


REGIONAL_TABLES = ("authors", "series", "books", "author_sources", "author_identity_decisions", "author_search_evidence")


def regional_path(user, region: str) -> Path:
    SettingsUpdate(region=region)  # Validate before using a region as a directory name.
    return user.data_path.parent / "regions" / region / "audiblecalendar.db"


def validate_region(database: Database, region: str) -> None:
    """Reject another marketplace instead of silently mixing libraries."""
    if load_settings(database).get("region") != region:
        raise ValueError(f"This database does not belong to the selected region ({region.upper()})")
    with database.transaction() as connection:
        for table in REGIONAL_TABLES:
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE marketplace NOT IN (?, 'demo') LIMIT 1", (region,)
            ).fetchone():
                raise ValueError("The database contains records from another region")


def _keep_region(database: Database, region: str, active_region: str) -> None:
    with database.transaction() as connection:
        for table in REGIONAL_TABLES:
            connection.execute(
                f"DELETE FROM {table} WHERE marketplace != ? AND NOT (marketplace = 'demo' AND ?)",
                (region, region == active_region),
            )
        for kind, table in (("author", "authors"), ("series", "series"), ("book", "books")):
            connection.execute(
                f"DELETE FROM sync_state WHERE target_type = ? AND target_id NOT IN (SELECT id FROM {table})",
                (kind,),
            )
        # Request/cache keys already contain their marketplace. Keep usable
        # responses without fetching the catalogue again during the split.
        obsolete = []
        for row in connection.execute("SELECT cache_key FROM source_cache"):
            try:
                parameters = json.loads(row["cache_key"])["parameters"]
                if parameters.get("region", region) == region:
                    continue
            except (KeyError, TypeError, ValueError, AttributeError):
                pass
            obsolete.append((row["cache_key"],))
        connection.executemany("DELETE FROM source_cache WHERE cache_key = ?", obsolete)
        if region != active_region:
            connection.execute("DELETE FROM app_runtime_state")
        connection.execute("DELETE FROM app_settings")
        save_settings(database, {"region": region}, connection=connection)
    database.compact()


def _copy_image(source: str, destination: str) -> str:
    # Cache refreshes replace files, so hard links save space without coupling
    # future changes. Fall back to a copy on filesystems without link support.
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def prepare_user_regions(user, defaults: dict) -> dict:
    """Preserve existing regional rows and IDs; never translate follow intent.

    The original database remains untouched as a pre-split backup. Publish all
    regional copies together so an interrupted conversion can safely retry.
    """
    if not user.data_path.exists():
        return dict(defaults)
    source = Database(user.data_path)
    source.initialize()
    values = {**defaults, **load_settings(source)}
    active_region = values["region"]
    regions_root = regional_path(user, active_region).parent.parent
    if regions_root.exists():
        return values
    with tempfile.TemporaryDirectory(prefix=".regional-split-", dir=user.data_path.parent) as directory:
        staging = Path(directory)
        snapshot = Database(staging / "snapshot.db")
        source.backup_to(snapshot.path)
        with snapshot.transaction() as connection:
            regions = {active_region}
            for table in REGIONAL_TABLES:
                regions.update(row[0] for row in connection.execute(f"SELECT DISTINCT marketplace FROM {table}"))
        regions.discard("demo")
        staged_regions = staging / "regions"
        for region in sorted(regions):
            regional_path(user, region)
            target = Database(staged_regions / region / "audiblecalendar.db")
            target.path.parent.mkdir(parents=True)
            snapshot.backup_to(target.path)
            _keep_region(target, region, active_region)
            validate_region(target, region)
            images = source.path.parent / "images"
            if images.is_dir():
                shutil.copytree(images, target.path.parent / "images", copy_function=_copy_image)
        staged_regions.replace(regions_root)
    return values
