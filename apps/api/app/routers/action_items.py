"""Action item tracker endpoints (#44, Agent2 Day 6).

Endpoints:
    POST /action-items/extract             paste notes → extract items
    POST /action-items/post-to-slack       post extracted items to a channel
    GET  /action-items                     list (filter by status, owner)
    PATCH /action-items/{id}               manual edits (status, owner, due)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import action_tracker

log = logging.getLogger(__name__)

router = APIRouter(prefix="/action-items", tags=["action-items"])


def _require_user(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found.")
    return org_id, user_id, token


# ── Extract ─────────────────────────────────────────────────────────────────


class ExtractRequest(BaseModel):
    notes: str = Field(..., min_length=10, max_length=40000)
    source_meeting_id: str | None = None


@router.post("/extract")
async def extract(
    body: ExtractRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_user(current_user)
    try:
        rows = await action_tracker.extract_action_items(
            org_id=org_id,
            user_id=user_id,
            notes=body.notes,
            source_meeting_id=body.source_meeting_id,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return {"action_items": rows}


# ── Post to Slack ─────────────────────────────────────────────────────────────


class PostToSlackRequest(BaseModel):
    action_item_ids: list[str] = Field(..., min_length=1, max_length=100)
    channel_id: str = Field(..., min_length=1)


@router.post("/post-to-slack")
async def post_to_slack(
    body: PostToSlackRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _ = _require_user(current_user)
    try:
        result = await action_tracker.post_action_items_to_slack(
            org_id=org_id,
            action_item_ids=body.action_item_ids,
            channel_id=body.channel_id,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return result


# ── Read + edit ─────────────────────────────────────────────────────────────


@router.get("")
async def list_items(
    status_filter: str | None = Query(default=None, alias="status"),
    owner_user_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_user(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        q = (
            client.table("action_items")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        if owner_user_id:
            q = q.eq("owner_user_id", owner_user_id)
        return q.execute().data or []

    return {"action_items": await asyncio.to_thread(_fetch)}


class UpdateActionItemRequest(BaseModel):
    action_text: str | None = None
    owner_user_id: str | None = None
    due_date: date | None = None
    status: Literal["pending", "completed", "cancelled"] | None = None


@router.patch("/{item_id}")
async def update_item(
    item_id: str,
    body: UpdateActionItemRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_user(current_user)
    svc = get_service_client()

    payload: dict[str, Any] = {}
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "due_date" and v is not None:
            payload[k] = v.isoformat()
        else:
            payload[k] = v
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update.")

    if payload.get("status") == "completed":
        from datetime import UTC, datetime as dt

        payload["completed_at"] = dt.now(UTC).isoformat()

    def _update() -> dict[str, Any] | None:
        res = (
            svc.table("action_items")
            .update(payload)
            .eq("id", item_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action item not found.")
    return row
