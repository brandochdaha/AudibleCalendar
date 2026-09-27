"""Transfer HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from ..database_transfer import DatabaseTransferError, create_export, import_archive
from ..settings_store import load_user_settings, values_from_settings
from ..api_context import _workspace

router = APIRouter()
MAX_ARCHIVE_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


def _remove_temporary_export(path: Path) -> None:
    path.unlink(missing_ok=True)


@router.get("/api/database/export", response_class=FileResponse)
def export_database(request: Request) -> FileResponse:
    workspace = _workspace(request)
    try:
        export_path = create_export(workspace.database, settings=values_from_settings(workspace.settings))
    except DatabaseTransferError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"audiblecalendar-{workspace.settings.region}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.zip"
    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=filename,
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(_remove_temporary_export, export_path),
    )


@router.put("/api/database/import")
async def import_database_route(request: Request, response: Response) -> dict[str, Any]:
    sync_schedule_changed = request.app.state.sync_schedule_changed
    workspace = _workspace(request)
    if workspace.sync_service.sync_in_progress or workspace.active_requests > 1:
        raise HTTPException(status_code=409, detail="Wait for current requests and sync to finish before importing a database")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_ARCHIVE_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="The archive is too large")
        except ValueError:
            pass

    descriptor, name = tempfile.mkstemp(
        prefix=".audiblecalendar-import-",
        suffix=".zip",
        dir=workspace.database.path.parent,
    )
    os.close(descriptor)
    import_path = Path(name)
    total = 0
    workspace.importing = True
    try:
        with import_path.open("wb") as destination:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="The archive is too large")
                await run_in_threadpool(destination.write, chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="Choose a database file to import")
        user_store = request.app.state.user_store
        backup_path = await run_in_threadpool(
            import_archive, workspace.database, import_path, user_store=user_store, user_id=workspace.user.id,
        )
        workspace.database.author_identities_dirty = True
        request.app.state.workspace_manager.activate(
            workspace, workspace.user, await run_in_threadpool(load_user_settings, user_store, workspace.user.id),
        )
        sync_schedule_changed.set()
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "ok",
            "message": "Regional library imported.",
            "backup": str(backup_path) if backup_path else None,
        }
    except DatabaseTransferError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        workspace.importing = False
        import_path.unlink(missing_ok=True)
