"""Current SQLite schema. Earlier database formats are not supported."""

from __future__ import annotations


from .author_sources import SOURCE_SCHEMA
from .author_decisions import DECISION_SCHEMA

LATEST_SCHEMA_VERSION = 9


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL,
    asin TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    audnexus_asin TEXT,
    is_tracked INTEGER NOT NULL DEFAULT 0,
    follow_all INTEGER NOT NULL DEFAULT 0,
    is_favourite INTEGER NOT NULL DEFAULT 0,
    source_payload TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (marketplace, asin)
);

CREATE TABLE IF NOT EXISTS author_aliases (
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    alias_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_id, alias_name)
);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL,
    asin TEXT NOT NULL,
    name TEXT NOT NULL,
    is_verified INTEGER NOT NULL DEFAULT 1,
    is_tracked INTEGER NOT NULL DEFAULT 0,
    is_favourite INTEGER NOT NULL DEFAULT 0,
    favourite_override INTEGER,
    source_payload TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (marketplace, asin)
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketplace TEXT NOT NULL,
    asin TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    description TEXT,
    release_date TEXT,
    release_date_precision TEXT NOT NULL DEFAULT 'day',
    image_url TEXT,
    audible_url TEXT,
    publisher TEXT,
    runtime_minutes INTEGER,
    language TEXT,
    is_preorder INTEGER NOT NULL DEFAULT 0,
    is_available INTEGER,
    is_tracked INTEGER NOT NULL DEFAULT 0,
    is_favourite INTEGER NOT NULL DEFAULT 0,
    favourite_override INTEGER,
    source_payload TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (marketplace, asin)
);

CREATE TABLE IF NOT EXISTS book_authors (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    role TEXT,
    PRIMARY KEY (book_id, author_id)
);

CREATE TABLE IF NOT EXISTS book_series (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    sequence_text TEXT,
    sequence_number REAL,
    PRIMARY KEY (book_id, series_id)
);

-- In broad follow_all mode, zero-valued rows are explicit exclusions.
-- When follow_all is disabled, one-valued rows form the explicit whitelist.
CREATE TABLE IF NOT EXISTS author_series_preferences (
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
    is_selected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_id, series_id)
);

-- Standalone books can be selected beneath an author and use the same
-- broad-mode exclusion / explicit-mode inclusion convention. Books that later
-- acquire a series relation are represented through the series scope while
-- retaining the original relationship for existing data.
CREATE TABLE IF NOT EXISTS author_book_preferences (
    author_id INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    is_selected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (author_id, book_id)
);

CREATE TABLE IF NOT EXISTS release_date_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    old_release_date TEXT,
    new_release_date TEXT,
    source TEXT NOT NULL,
    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_state (
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    last_started_at TEXT,
    last_success_at TEXT,
    last_error TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (target_type, target_id)
);

CREATE TABLE IF NOT EXISTS source_cache (
    cache_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS image_cache (
    image_url TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_runtime_state (
    state_key TEXT PRIMARY KEY,
    state_value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_authors_favourite ON authors(is_favourite);
CREATE INDEX IF NOT EXISTS idx_series_favourite ON series(is_favourite);
CREATE INDEX IF NOT EXISTS idx_books_favourite ON books(is_favourite);
CREATE INDEX IF NOT EXISTS idx_source_cache_operation ON source_cache(source, operation);
CREATE INDEX IF NOT EXISTS idx_books_release_date ON books(release_date);
CREATE INDEX IF NOT EXISTS idx_books_tracked ON books(is_tracked);
CREATE INDEX IF NOT EXISTS idx_authors_tracked ON authors(is_tracked);
CREATE INDEX IF NOT EXISTS idx_series_tracked ON series(is_tracked);
CREATE INDEX IF NOT EXISTS idx_author_series_preferences_author ON author_series_preferences(author_id);
CREATE INDEX IF NOT EXISTS idx_author_series_preferences_series ON author_series_preferences(series_id);
CREATE INDEX IF NOT EXISTS idx_author_book_preferences_author ON author_book_preferences(author_id);
CREATE INDEX IF NOT EXISTS idx_author_book_preferences_book ON author_book_preferences(book_id);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
CREATE INDEX IF NOT EXISTS idx_book_series_series ON book_series(series_id);
CREATE INDEX IF NOT EXISTS idx_source_cache_fetched_at ON source_cache(fetched_at);
CREATE INDEX IF NOT EXISTS idx_image_cache_fetched_at ON image_cache(fetched_at);
"""

SCHEMA += DECISION_SCHEMA + SOURCE_SCHEMA
