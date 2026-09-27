"""Per-request account and workspace access."""

from fastapi import Request, HTTPException
from .users import UserRecord
from .workspaces import Workspace


def _workspace(request: Request) -> Workspace:
    workspace = getattr(request.state, "workspace", None)
    if workspace is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return workspace


def _user(request: Request) -> UserRecord:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _require_admin(request: Request) -> UserRecord:
    user = _user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user
