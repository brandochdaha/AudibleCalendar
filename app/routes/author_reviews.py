"""Account-scoped confirmation of uncertain author identities."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..api_context import _workspace
from ..api_models import AuthorSearchDecisionRequest, AuthorSplitRequest
from ..author_management import list_author_management
from ..author_reviews import (
    decide_author_review,
    decide_author_search_match,
    list_author_reviews,
    split_author_management_group,
)

router = APIRouter()


class AuthorDecision(BaseModel):
    review_id: str = Field(min_length=64, max_length=64)
    decision: Literal['merge', 'separate']


@router.get('/api/author-reviews')
def pending_author_reviews(request: Request):
    workspace = _workspace(request)
    return {'items': list_author_reviews(workspace.database, workspace.settings.region)}


@router.post('/api/author-reviews')
async def confirm_author_review(payload: AuthorDecision, request: Request):
    workspace = _workspace(request)
    if workspace.sync_service.sync_in_progress or workspace.active_requests > 1:
        raise HTTPException(409, 'Wait for current requests and sync to finish before changing author identities')
    # This short local transaction runs without yielding, so a sync or another
    # request cannot start between the busy check and the identity change.
    try:
        decide_author_review(workspace.database, workspace.settings.region, payload.review_id, payload.decision)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'ok'}


@router.post('/api/author-search-decision')
async def confirm_author_search_decision(payload: AuthorSearchDecisionRequest, request: Request):
    workspace = _workspace(request)
    if workspace.sync_service.sync_in_progress or workspace.active_requests > 1:
        raise HTTPException(409, 'Wait for current requests and sync to finish before changing author identities')
    try:
        decide_author_search_match(
            workspace.database,
            workspace.settings.region,
            payload.primary_asin,
            payload.members,
            payload.decision,
            manual=payload.manual,
            override_separation=payload.override_separation,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'ok'}


@router.get('/api/author-management/merged')
def merged_author_management(request: Request):
    workspace = _workspace(request)
    result = list_author_management(
        workspace.database,
        workspace.settings.region,
        workspace.settings.author_merge_min_shared_series,
        include_candidates=False,
    )
    return {'items': result['merged']}


@router.get('/api/author-management/candidates')
def author_management_candidates(request: Request):
    workspace = _workspace(request)
    result = list_author_management(
        workspace.database,
        workspace.settings.region,
        workspace.settings.author_merge_min_shared_series,
        include_merged=False,
    )
    return {'items': result['candidates']}


@router.post('/api/author-management-decision')
async def confirm_author_management_decision(payload: AuthorSearchDecisionRequest, request: Request):
    workspace = _workspace(request)
    if workspace.sync_service.sync_in_progress or workspace.active_requests > 1:
        raise HTTPException(409, 'Wait for current requests and sync to finish before changing author identities')
    try:
        decide_author_search_match(
            workspace.database,
            workspace.settings.region,
            payload.primary_asin,
            payload.members,
            payload.decision,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'ok'}


@router.post('/api/author-management/split')
async def split_author_management(payload: AuthorSplitRequest, request: Request):
    workspace = _workspace(request)
    if workspace.sync_service.sync_in_progress or workspace.active_requests > 1:
        raise HTTPException(409, 'Wait for current requests and sync to finish before changing author identities')
    try:
        split_author_management_group(
            workspace.database,
            workspace.settings.region,
            payload.primary_asin,
            payload.split_asins,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'status': 'ok'}
