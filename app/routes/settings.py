"""Settings HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter
from typing import Any
from fastapi import HTTPException, Request, Response
from ..api_models import ClearCacheRequest, SettingsUpdate
from ..category_catalog import category_by_id, category_options
from ..auth import set_session_cookie
from starlette.concurrency import run_in_threadpool
from ..settings_store import save_user_settings, values_from_settings
from ..storage_policy import StoragePolicy, save_storage_policy
from ..storage import measure_cache_usage
from ..cache_management import clear_cache
from ..api_context import _workspace, _user, _require_admin

router = APIRouter()


@router.post("/api/cache/clear")
async def clear_cached_data(request: Request, selection: ClearCacheRequest) -> dict[str, Any]:
    _require_admin(request)
    manager = request.app.state.workspace_manager
    current = _workspace(request)
    if manager.maintaining or any(
        workspace.active_requests > (1 if workspace is current else 0)
        or workspace.importing or workspace.changing_settings or workspace.sync_service.sync_in_progress
        for workspace in manager._workspaces.values()
    ):
        raise HTTPException(status_code=409, detail="Wait for current requests, syncs or imports to finish, then try again")
    manager.maintaining = True
    try:
        return {"status": "ok", **await run_in_threadpool(
            clear_cache,
            manager,
            clear_responses=selection.cleanup_enabled,
            clear_artwork=selection.artwork_expiry_enabled,
        )}
    finally:
        manager.maintaining = False


@router.get("/api/settings")
def get_settings(request: Request) -> dict[str, Any]:
    settings_defaults = request.app.state.settings_defaults
    workspace = _workspace(request)
    user = _user(request)
    user_settings = workspace.settings
    return {
        "values": values_from_settings(user_settings),
        "browser_time_zone_pending": (
            user_settings.detect_browser_time_zone
            and request.app.state.user_store.browser_time_zone_pending(user)
        ),
        "defaults": settings_defaults,
        "auth": {
            "username": user.username,
            "password_is_default": user.password_is_default,
        },
        "user": user.summary(),
        "storage": request.app.state.workspace_manager.storage_status if user.is_admin else None,
        "storage_usage": measure_cache_usage(request.app.state.workspace_manager) if user.is_admin else None,
        "storage_policy": request.app.state.workspace_manager.storage_policy.model_dump() if user.is_admin else None,
        "storage_defaults": StoragePolicy().model_dump() if user.is_admin else None,
        "region_options": [
            {"value": "us", "label": "United States (audible.com)"},
            {"value": "uk", "label": "United Kingdom (audible.co.uk)"},
            {"value": "ca", "label": "Canada (audible.ca)"},
            {"value": "au", "label": "Australia (audible.com.au)"},
            {"value": "de", "label": "Germany (audible.de)"},
            {"value": "fr", "label": "France (audible.fr)"},
            {"value": "es", "label": "Spain (audible.es)"},
            {"value": "it", "label": "Italy (audible.it)"},
            {"value": "jp", "label": "Japan (audible.co.jp)"},
            {"value": "in", "label": "India (audible.co.in)"},
            {"value": "br", "label": "Brazil (audible.com.br)"},
        ],
        "date_display_options": [
            {"value": "uk", "label": "UK — 11 Aug 2026"},
            {"value": "us", "label": "US — Aug 11, 2026"},
            {"value": "iso", "label": "ISO — 2026-08-11"},
        ],
        "time_zone_options": [
            {"value": "America/Los_Angeles", "label": "Pacific — Los Angeles"},
            {"value": "America/Denver", "label": "Mountain — Denver"},
            {"value": "America/Chicago", "label": "Central — Chicago"},
            {"value": "America/New_York", "label": "Eastern — New York"},
            {"value": "America/Toronto", "label": "Eastern — Toronto"},
            {"value": "Europe/London", "label": "London"},
            {"value": "Europe/Berlin", "label": "Berlin"},
            {"value": "Europe/Paris", "label": "Paris"},
            {"value": "Europe/Madrid", "label": "Madrid"},
            {"value": "Europe/Rome", "label": "Rome"},
            {"value": "Asia/Tokyo", "label": "Tokyo"},
            {"value": "Asia/Kolkata", "label": "Kolkata"},
            {"value": "Australia/Sydney", "label": "Sydney"},
            {"value": "America/Sao_Paulo", "label": "São Paulo"},
            {"value": "UTC", "label": "UTC"},
        ],
        "category_options": category_options(),
    }


@router.put("/api/settings")
async def update_settings(request: SettingsUpdate, http_request: Request, response: Response) -> dict[str, Any]:
    user_store = http_request.app.state.user_store
    manager = http_request.app.state.workspace_manager
    settings_defaults = http_request.app.state.settings_defaults
    sync_schedule_changed = http_request.app.state.sync_schedule_changed
    workspace = _workspace(http_request)
    user = _user(http_request)
    user_settings = workspace.settings
    values = request.model_dump(exclude_none=True)
    values.pop("storage_policy", None)
    storage_policy = request.storage_policy
    if storage_policy is not None and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only the administrator can change cleanup settings")
    auth_username = values.pop("auth_username", None)
    auth_password = values.pop("auth_password", None)
    if "default_category_id" in values and category_by_id(values["default_category_id"]) is None:
        raise HTTPException(status_code=400, detail="Unknown default Audible category")
    requested_region = values.get("region")
    if requested_region and requested_region != user_settings.region and (workspace.sync_service.sync_in_progress or workspace.active_requests > 1):
        raise HTTPException(status_code=409, detail="Wait for current catalog requests to finish before saving settings")
    credentials_changed = (auth_username is not None and auth_username.strip() != user.username) or auth_password is not None
    next_values = {**values_from_settings(user_settings), **values}
    if user.password_is_default and not auth_password:
        raise HTTPException(status_code=400, detail="Choose a new password to finish account setup")

    def persist():
        updated_user = user
        next_workspace = (manager.open_region(user, next_values)
                          if next_values["region"] != user_settings.region else workspace)
        with user_store.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if credentials_changed:
                updated_user = user_store.update(user.id, username=auth_username, password=auth_password, connection=connection)
            save_user_settings(user_store, user.id, values, connection=connection)
            if storage_policy is not None:
                save_storage_policy(connection, storage_policy)
        token = user_store.issue_session(updated_user.id) if credentials_changed else None
        return next_workspace, updated_user, token

    workspace.changing_settings = True
    try:
        next_workspace, updated_user, token = await run_in_threadpool(persist)
        manager.activate(next_workspace, updated_user, next_values)
        if storage_policy is not None:
            manager.storage_policy = storage_policy
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        workspace.changing_settings = False
    user_settings = next_workspace.settings
    if token:
        set_session_cookie(response, http_request, token)
        http_request.state.user = updated_user
    sync_schedule_changed.set()
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": "ok",
        "values": values_from_settings(user_settings),
        "defaults": settings_defaults,
        "auth": {
            "username": updated_user.username,
            "password_is_default": updated_user.password_is_default,
        },
        "user": updated_user.summary(),
        "storage_policy": manager.storage_policy.model_dump() if updated_user.is_admin else None,
    }
