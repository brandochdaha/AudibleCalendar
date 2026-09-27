"""Compact list responses without changing internal repository records."""

from contextlib import contextmanager
from contextvars import ContextVar

summary_mode = ContextVar("catalogue_summary_mode", default=False)
INTERNAL_FIELDS = {"source_payload", "description", "created_at", "updated_at", "last_seen_at"}


@contextmanager
def summaries():
    token = summary_mode.set(True)
    try:
        yield
    finally:
        summary_mode.reset(token)


def columns(connection, table: str, alias: str = "") -> str:
    if not summary_mode.get():
        return f"{alias}.*" if alias else "*"
    # Table and alias names are supplied only by repository code.
    prefix = f"{alias}." if alias else ""
    return ", ".join(prefix + row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
                     if row[1] not in INTERNAL_FIELDS)
