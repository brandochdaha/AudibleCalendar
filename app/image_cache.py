"""Bounded artwork downloads and shared public artwork, with portable user copies."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from starlette.concurrency import run_in_threadpool

from .db import Database
from .sources.http import HttpPool

MAX_IMAGE_BYTES = 12 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}
_IMAGE_HOST_SUFFIXES = (
    ".media-amazon.com", ".ssl-images-amazon.com", ".audnex.us", ".audible.com",
    ".audible.co.uk", ".audible.ca", ".audible.com.au", ".audible.de", ".audible.fr",
    ".audible.es", ".audible.it", ".audible.co.jp", ".audible.co.in", ".audible.com.br",
)


class ImageCacheError(RuntimeError):
    """An artwork request or cache entry is unsafe or unavailable."""


@dataclass(frozen=True)
class CachedImage:
    path: Path
    content_type: str


def is_allowed_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        return False
    return host == "images.amazon.com" or any(host.endswith(suffix) for suffix in _IMAGE_HOST_SUFFIXES)


class ImageCache:
    _locks = weakref.WeakValueDictionary()

    def __init__(self, database: Database, *, shared_directory: Path | None = None, http_pool=None):
        self.database = database
        self.directory = database.path.parent / "images"
        self.shared_directory = shared_directory
        self.http_pool = http_pool or HttpPool()

    @staticmethod
    def file_name(image_url: str) -> str:
        return hashlib.sha256(image_url.encode("utf-8")).hexdigest()

    def read(self, image_url: str) -> CachedImage | None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT file_name, content_type FROM image_cache WHERE image_url = ?", (image_url,)).fetchone()
        local = None
        if row:
            path = self.directory / self.file_name(image_url)
            if (row["file_name"] == path.name and row["content_type"] in IMAGE_TYPES
                    and path.is_file() and not path.is_symlink()
                    and path.resolve().parent == self.directory.resolve()
                    and path.stat().st_size <= MAX_IMAGE_BYTES):
                local = CachedImage(path, row["content_type"])
        # Public images are shared, but imported per-user metadata never selects
        # a shared filename or publishes untrusted bytes into the shared cache.
        if self.shared_directory:
            for content_type, suffix in IMAGE_TYPES.items():
                path = self.shared_directory / (self.file_name(image_url) + suffix)
                if (path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_IMAGE_BYTES
                        and (local is None or path.stat().st_mtime > local.path.stat().st_mtime)):
                    cached = self.write(image_url, path.read_bytes(), content_type)
                    shutil.copystat(path, cached.path)
                    return cached
        return local

    def write(self, image_url: str, content: bytes, content_type: str) -> CachedImage:
        if content_type not in IMAGE_TYPES or len(content) > MAX_IMAGE_BYTES:
            raise ImageCacheError("Artwork was not a supported, bounded raster image")
        self.directory.mkdir(parents=True, exist_ok=True)
        file_name = self.file_name(image_url)
        path = self.directory / file_name
        temporary = self.directory / f".{file_name}.tmp"
        temporary.write_bytes(content)
        temporary.replace(path)
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO image_cache (image_url, file_name, content_type, fetched_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(image_url) DO UPDATE SET file_name=excluded.file_name,
                    content_type=excluded.content_type, fetched_at=excluded.fetched_at""",
                (image_url, file_name, content_type),
            )
        return CachedImage(path, content_type)

    def _store_download(self, image_url: str, content: bytes, content_type: str) -> CachedImage:
        cached = self.write(image_url, content, content_type)
        if self.shared_directory:
            self.shared_directory.mkdir(parents=True, exist_ok=True)
            shared = self.shared_directory / (self.file_name(image_url) + IMAGE_TYPES[content_type])
            temporary = shared.with_suffix(shared.suffix + ".tmp")
            shutil.copy2(cached.path, temporary)
            temporary.replace(shared)
            for suffix in IMAGE_TYPES.values():
                old = self.shared_directory / (self.file_name(image_url) + suffix)
                if old != shared:
                    old.unlink(missing_ok=True)
        return cached

    async def get_or_fetch(self, image_url: str, timeout: float, refresh: bool = False) -> CachedImage:
        if not is_allowed_image_url(image_url):
            raise ImageCacheError("Image URL is not from an approved Audible artwork host")
        requested_at = time.time()
        lock_key = (str(self.shared_directory or self.directory), image_url)
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            cached = await run_in_threadpool(self.read, image_url)
            if cached and (not refresh or cached.path.stat().st_mtime >= requested_at):
                return cached
            current_url = image_url
            try:
                for _ in range(6):
                    if not is_allowed_image_url(current_url):
                        raise ImageCacheError("Artwork redirect is not from an approved host")
                    async with self.http_pool.client().stream("GET", current_url, timeout=timeout) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ImageCacheError("Artwork redirect has no destination")
                            current_url = str(response.url.join(location))
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type not in IMAGE_TYPES:
                            raise ImageCacheError("Artwork response was not a supported raster image")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > MAX_IMAGE_BYTES:
                                raise ImageCacheError("Artwork response is too large")
                            content.extend(chunk)
                        return await run_in_threadpool(self._store_download, image_url, bytes(content), content_type)
                raise ImageCacheError("Artwork has too many redirects")
            except (httpx.HTTPError, ValueError) as exc:
                raise ImageCacheError(f"Artwork request failed: {exc}") from exc
