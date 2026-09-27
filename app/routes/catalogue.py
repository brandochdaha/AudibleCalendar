"""Catalogue HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter
from datetime import timedelta
from typing import Any, Literal
from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse
from ..api_models import AuthorGroupWatchRequest, AuthorSeriesRequest, CategoryUpcomingRequest, FavouriteRequest, UnfollowRequest, WatchRequest
from ..category_catalog import DEFAULT_CATEGORY_ID, category_by_id, category_options
from ..image_cache import ImageCacheError, is_allowed_image_url
from ..repository import calendar, set_author_favourite, set_book_favourite, set_series_favourite, stop_following, sync_state, unfollow_item, unfollow_preview
from ..repository.watchlist import watchlist
from ..sync_runtime import read_sync_runtime
from ..repository.presentation import summaries
from ..input_validation import release_date
from ..api_context import _workspace
from ..author_reviews import list_author_reviews
from ..time_zone import local_today

router = APIRouter()


@router.get("/api/config")
def config(request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    settings = workspace.settings
    return {
        "region": settings.region,
        "demo_data": settings.demo_data,
        "audnexus_enabled": settings.audnexus_enabled,
        "watchlist_chips_always_visible": settings.watchlist_chips_always_visible,
        "header_sync_summary_visible": settings.header_sync_summary_visible,
        "header_sync_dates_visible": settings.header_sync_dates_visible,
        "header_sync_date_style": settings.header_sync_date_style,
        "close_settings_on_save": settings.close_settings_on_save,
        "automatic_sync_enabled": settings.automatic_sync_enabled,
        "sync_interval_hours": settings.sync_interval_hours,
        "favourites_sync_enabled": settings.favourites_sync_enabled,
        "favourites_sync_interval_hours": settings.favourites_sync_interval_hours,
        "date_display": settings.date_display,
        "time_zone": settings.time_zone,
        "default_release_scope": settings.default_release_scope,
        "default_category_id": settings.default_category_id,
        "default_category_window": settings.default_category_window,
        "default_category_days": settings.default_category_days,
        "cache": {
            "detail_minutes": settings.cache_detail_minutes,
            "search_minutes": settings.cache_search_minutes,
            "author_hours": settings.cache_author_hours,
            "series_hours": settings.cache_series_hours,
            "upcoming_hours": settings.cache_upcoming_hours,
            "far_future_hours": settings.cache_far_future_hours,
            "released_days": settings.cache_released_days,
        },
    }


@router.get("/api/image", response_class=FileResponse)
async def image(
    request: Request,
    url: str = Query(min_length=1, max_length=2048),
    refresh: bool = Query(default=False),
) -> FileResponse:
    """Serve approved catalogue artwork through the persistent local cache."""

    if not is_allowed_image_url(url):
        raise HTTPException(status_code=400, detail="Image URL is not from an approved Audible artwork host")
    try:
        workspace = _workspace(request)
        cached = await workspace.image_cache.get_or_fetch(url, workspace.settings.audible_timeout, refresh=refresh)
    except ImageCacheError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FileResponse(
        cached.path,
        media_type=cached.content_type,
        headers={
            "Cache-Control": "no-store" if refresh else "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/watches")
def watches(request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    with summaries():
        result = watchlist(workspace.database)
    result['author_review_count'] = len(list_author_reviews(workspace.database, workspace.settings.region))
    return result


@router.get("/api/calendar")
def calendar_route(
    request: Request,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    include_past: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=500),
    descending: bool = Query(default=False),
    favourites_only: bool = Query(default=False),
) -> dict[str, Any]:
    workspace = _workspace(request)
    today = local_today(workspace.settings.time_zone)
    start_date = start or today.replace(day=1).isoformat()
    end_date = end or (today + timedelta(days=120)).isoformat()
    try:
        release_date(start_date)
        release_date(end_date)
        if start_date > end_date:
            raise ValueError("Reversed date range")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start and end must be ISO dates") from exc
    with summaries():
        items = calendar(workspace.database, start_date, end_date, workspace.settings.english_only, include_past, limit=limit, descending=descending, favourites_only=favourites_only, today=today)
    return {
        "start": start_date,
        "end": end_date,
        "items": items,
    }


@router.get("/api/categories")
def categories(request: Request) -> dict[str, Any]:
    """Return the local Audible genre snapshot without an upstream request."""

    user_settings = _workspace(request).settings
    default_id = user_settings.default_category_id if category_by_id(user_settings.default_category_id) else DEFAULT_CATEGORY_ID
    return {"default_id": default_id, "categories": category_options()}


@router.post("/api/category-upcoming")
async def category_upcoming(request: CategoryUpcomingRequest, http_request: Request) -> dict[str, Any]:
    """Manually load a broad category overview; it never writes user lists."""

    try:
        return await _workspace(http_request).sync_service.category_upcoming(request.category_id, request.days, force=request.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/category-upcoming")
def cached_category_upcoming(
    request: Request,
    category_id: str = Query(min_length=1, max_length=32),
    days: int = Query(default=365, ge=1, le=3650),
) -> dict[str, Any]:
    """Return the last explicitly checked category overview, if one exists."""

    try:
        result = _workspace(request).sync_service.cached_category_upcoming(category_id, days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        return {"cached": False}
    return {"cached": True, **result}


@router.get("/api/search")
async def search(
    request: Request,
    q: str = Query(min_length=1, max_length=120),
    kind: Literal["author", "series", "book"] = "book",
) -> dict[str, Any]:
    try:
        return {"kind": kind, "query": q, "items": await _workspace(request).sync_service.search(q, kind)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/author-details")
async def author_details(
    request: Request,
    asin: str = Query(min_length=1, max_length=80),
    name: str = Query(min_length=1, max_length=160),
    member_asin: list[str] = Query(default=[]),
    member_name: list[str] = Query(default=[]),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return await _workspace(request).sync_service.author_details(
            asin,
            name,
            member_asins=member_asin,
            member_names=member_name,
            refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/series-details")
async def series_details(
    request: Request,
    asin: str = Query(min_length=1, max_length=80),
    name: str = Query(default="", max_length=160),
    seed_asin: str = Query(default="", max_length=80),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return await _workspace(request).sync_service.series_details(asin, name, seed_asin, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/book-details")
async def book_details(
    request: Request,
    asin: str = Query(min_length=1, max_length=80),
    name: str = Query(default="", max_length=240),
    refresh: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        return await _workspace(request).sync_service.book_details(asin, name, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/watch")
async def add_watch(request: WatchRequest, http_request: Request) -> dict[str, Any]:
    try:
        payload = request.model_dump(exclude_none=True)
        result = await _workspace(http_request).sync_service.add_watch(request.kind, payload)
        return {"status": "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/watch/author-group")
async def add_author_group_watch(request: AuthorGroupWatchRequest, http_request: Request) -> dict[str, Any]:
    try:
        members = [member.model_dump(exclude_none=True) for member in request.members]
        result = await _workspace(http_request).sync_service.add_author_group_watch(members)
        return {"status": "ok", **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/author-series")
async def author_series(request: AuthorSeriesRequest, http_request: Request) -> dict[str, Any]:
    try:
        return await _workspace(http_request).sync_service.set_author_series(request.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.put("/api/favourite")
def update_favourite(request: FavouriteRequest, http_request: Request) -> dict[str, str]:
    database = _workspace(http_request).database
    try:
        if request.kind == "author":
            set_author_favourite(database, request.entity_id, request.is_favourite)
        elif request.kind == "series":
            set_series_favourite(database, request.entity_id, request.is_favourite)
        else:
            set_book_favourite(database, request.entity_id, request.is_favourite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.delete("/api/watch/{kind}/{entity_id}")
def remove_watch(kind: Literal["author", "series", "book"], entity_id: int, request: Request) -> dict[str, str]:
    database = _workspace(request).database
    try:
        stop_following(database, kind, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.get("/api/unfollow-preview/{kind}/{entity_id}")
def preview_unfollow(kind: Literal["author", "series", "book"], entity_id: int, request: Request) -> dict[str, Any]:
    database = _workspace(request).database
    try:
        return unfollow_preview(database, kind, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/unfollow")
def confirm_unfollow(request: UnfollowRequest, http_request: Request) -> dict[str, str]:
    database = _workspace(http_request).database
    try:
        unfollow_item(
            database,
            request.kind,
            request.entity_id,
            author_ids=request.author_ids,
            remove_direct=request.remove_direct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@router.post("/api/sync")
async def sync(request: Request) -> dict[str, Any]:
    sync_schedule_changed = request.app.state.sync_schedule_changed
    workspace = _workspace(request)
    # Both entry points fetch fresh discovery pages while reusing product metadata.
    try:
        return await workspace.sync_service.sync_all()
    finally:
        workspace.next_scheduled_sync_at = None
        sync_schedule_changed.set()


@router.post("/api/sync/scheduled")
async def scheduled_style_sync(request: Request) -> dict[str, Any]:
    sync_schedule_changed = request.app.state.sync_schedule_changed
    workspace = _workspace(request)
    try:
        return await workspace.sync_service.sync_all(scope=workspace.automatic_sync_scope())
    finally:
        workspace.next_scheduled_sync_at = None
        sync_schedule_changed.set()


@router.get("/api/sync/status")
def sync_status(request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    runtime = read_sync_runtime(workspace.database)
    scheduled_at = workspace.next_scheduled_sync_at
    if workspace.automatic_sync_scope() and scheduled_at is None and not workspace.sync_service.sync_in_progress:
        scheduled_at = workspace.automatic_sync_due_at()
    return {
        "items": sync_state(workspace.database),
        "scheduler_error": (request.app.state.workspace_manager.scheduler_errors.get(workspace.user.id)
                            or request.app.state.workspace_manager.scheduler_error),
        **runtime,
        "in_progress": workspace.sync_service.sync_in_progress,
        "progress": workspace.sync_service.sync_progress(),
        "source_metrics": workspace.sync_service.source_metrics(),
        "source_metric_breakdown": workspace.sync_service.source_metric_breakdown(),
        "automatic_sync_enabled": workspace.settings.automatic_sync_enabled,
        "favourites_sync_enabled": workspace.settings.favourites_sync_enabled,
        "automatic_sync_scope": workspace.automatic_sync_scope(),
        "next_scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
    }
