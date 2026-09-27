"""FastAPI entry point for AudibleCalendar."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .http_security import HttpSecurity

from . import __version__
from .auth import AUTH_COOKIE
from .login_throttle import LoginThrottle
from .config import Settings
from .users import UserStore
from .workspaces import WorkspaceManager
from .scheduler import scheduled_sync


BASE_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "AudibleCalendar"


logger = logging.getLogger("audiblecalendar")
base_settings = Settings.from_env()
user_store = UserStore(base_settings.database_path, demo_data=base_settings.demo_data)
workspace_manager = WorkspaceManager(user_store, base_settings)
settings_defaults = dict(workspace_manager.defaults)
sync_schedule_changed = asyncio.Event()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(scheduled_sync(workspace_manager, sync_schedule_changed))
    _app.state.scheduler_task = task
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        sync_tasks = [workspace.sync_service._sync_task for workspace in workspace_manager._workspaces.values()
                      if workspace.sync_service._sync_task is not None]
        for sync_task in sync_tasks:
            sync_task.cancel()
        await asyncio.gather(*sync_tasks, return_exceptions=True)
        await workspace_manager.http_pool.close()


app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/ready",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/logout",
}


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API_PATHS:
        if workspace_manager.maintaining:
            return JSONResponse(status_code=503, content={"detail": "Storage maintenance is running; retry shortly"}, headers={"Retry-After": "5"})
        user = await run_in_threadpool(user_store.session_user, request.cookies.get(AUTH_COOKIE))
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"Cache-Control": "no-store"},
            )
        if user.password_is_default and path not in {"/api/settings", "/api/config"}:
            return JSONResponse(status_code=403, content={"detail": "Change your initial password in Settings to finish account setup", "code": "setup_required"}, headers={"Cache-Control": "no-store"})
        workspace = await run_in_threadpool(workspace_manager.get, user)
        workspace = workspace_manager.current(user.id) or workspace
        if workspace_manager.maintaining:
            return JSONResponse(status_code=503, content={"detail": "Storage maintenance is running; retry shortly"}, headers={"Retry-After": "5"})
        requested_region = request.headers.get("x-audible-region")
        if path == "/api/database/export":
            requested_region = requested_region or request.query_params.get("region")
        if requested_region and requested_region != workspace.settings.region:
            return JSONResponse(status_code=409, content={
                "detail": "Your region changed. Reload the page to use the selected regional library.",
                "code": "region_changed",
            }, headers={"Cache-Control": "no-store"})
        if workspace.importing or workspace.changing_settings:
            return JSONResponse(status_code=409, content={"detail": "Library settings or database are being updated"})
        request.state.user = user
        request.state.workspace = workspace
        workspace.active_requests += 1
        try:
            response = await call_next(request)
            response.headers.setdefault("Cache-Control", "no-store")
        except BaseException:
            workspace.active_requests -= 1
            raise
        body = response.body_iterator

        async def counted_body():
            try:
                async for chunk in body:
                    yield chunk
            finally:
                workspace.active_requests -= 1

        response.body_iterator = counted_body()
        return response
    return await call_next(request)


@app.get("/", response_class=FileResponse)
def index() -> Path:
    return BASE_DIR / "static" / "index.html"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": APP_NAME}


@app.get("/api/ready")
def ready(request: Request) -> JSONResponse:
    task = getattr(request.app.state, "scheduler_task", None)
    available = bool(task and not task.done() and not workspace_manager.scheduler_error
                     and not workspace_manager.scheduler_errors)
    return JSONResponse({"status": "ok" if available else "degraded", "service": APP_NAME},
                        status_code=200 if available else 503, headers={"Cache-Control": "no-store"})

from .routes import accounts, author_reviews, catalogue, settings as settings_routes, transfer

for router in (accounts.router, author_reviews.router, catalogue.router, settings_routes.router, transfer.router):
    app.include_router(router)

app.state.user_store = user_store
app.state.login_throttle = LoginThrottle()
app.state.workspace_manager = workspace_manager
app.state.settings_defaults = settings_defaults
app.state.sync_schedule_changed = sync_schedule_changed

app.add_middleware(HttpSecurity)
