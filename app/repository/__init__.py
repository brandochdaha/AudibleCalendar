"""Public repository API, implemented in focused responsibility modules."""

from .common import (
    _now,
    _json,
    _decode,
    _series_favourite_sql,
    _author_series_scope_sql,
    _author_book_scope_sql,
)
from .relations import (
    _book_with_relations,
    _books_with_relations,
)
from .named import (
    _upsert_named,
    upsert_author,
    upsert_series,
)
from .preferences import (
    list_author_series_preferences,
    set_author_series_preference,
    list_author_book_preferences,
    set_author_book_preference,
    set_author_follow_all,
    clear_author_release_selections,
    list_selected_author_book_ids,
    list_author_followed_scopes,
    author_has_scope_overrides,
    _upsert_series_selection,
    _upsert_book_selection,
)
from .scopes import (
    _known_author_series,
    _known_author_standalone_books,
    _tracked_author_scopes,
    _series_authors,
    _book_authors,
    _author_with_aliases,
    _author_is_orphaned_by,
)
from .books import (
    _reconcile_followed_book,
    attach_missing_book_series,
    upsert_book,
)
from .favourites import (
    _clear_orphaned_favourites,
    set_author_favourite,
    set_series_favourite,
    set_book_favourite,
)
from .unfollow import (
    _unfollow_series_connection,
    _unfollow_book_connection,
    unfollow_preview,
    unfollow_item,
    stop_following,
)
from .queries import (
    _list,
    list_authors,
    list_series,
    list_selected_author_series,
    list_followed_series,
    list_books,
    get_book,
    list_followed_books,
    calendar,
    search_local,
    sync_state,
    update_sync_state,
)
