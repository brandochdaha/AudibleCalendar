"""Accounts HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter
from typing import Any
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from ..api_models import LoginRequest, UserCreateRequest, UserUpdateRequest
from ..auth import AUTH_COOKIE, set_session_cookie
from ..api_context import _require_admin

router = APIRouter()


@router.get("/api/auth/session")
def auth_session(request: Request) -> JSONResponse:
    user_store = request.app.state.user_store
    user = user_store.session_user(request.cookies.get(AUTH_COOKIE))
    payload: dict[str, Any] = {
        "authenticated": user is not None,
        "username": user.username if user else None,
    }
    if user:
        payload.update(user.summary())
    return JSONResponse(
        payload,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/auth/login")
def login(request: LoginRequest, http_request: Request, response: Response) -> dict[str, Any]:
    client = http_request.client.host if http_request.client else "unknown"
    retry_after = http_request.app.state.login_throttle.reserve(client, request.username)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again shortly.",
                            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"})
    user_store = http_request.app.state.user_store
    user = user_store.authenticate(request.username, request.password)
    if user is None:
        response.status_code = 401
        response.headers["Cache-Control"] = "no-store"
        return {"detail": "Invalid username or password"}
    token = user_store.issue_session(user.id)
    set_session_cookie(response, http_request, token)
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, **user.summary()}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    user_store = request.app.state.user_store
    user_store.revoke_session(request.cookies.get(AUTH_COOKIE))
    response.delete_cookie(AUTH_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}


@router.get("/api/users")
def list_users(request: Request) -> dict[str, Any]:
    user_store = request.app.state.user_store
    _require_admin(request)
    return {"users": [user.summary() for user in user_store.list()]}


@router.post("/api/users")
def create_user(request: UserCreateRequest, http_request: Request) -> dict[str, Any]:
    user_store = http_request.app.state.user_store
    _require_admin(http_request)
    try:
        user = user_store.create(request.username, request.password, role=request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": user.summary()}


@router.put("/api/users/{user_id}")
def update_user(user_id: int, request: UserUpdateRequest, http_request: Request) -> dict[str, Any]:
    user_store = http_request.app.state.user_store
    _require_admin(http_request)
    if user_store.get(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        updated = user_store.update(
            user_id,
            username=request.username,
            password=request.password,
            active=request.active,
            role=request.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user": updated.summary()}
