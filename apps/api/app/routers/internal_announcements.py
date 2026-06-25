"""Internal Communications Agent endpoints (Agent2 Day 4 #23).

Admin-only: creators in this codebase are typically admins, and the
announcement targets the whole org. We gate with role='admin' on every
endpoint to match the Day-4 sign-off (single-user approval = the admin
drafts + schedules in one motion).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import internal_comms as comms_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["internal-announcements"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only.")


# ── Models ───────────────────────────────────────────────────────────────


class AnnouncementGenerate(BaseModel):
    request_text: str = Field(min_length=1, max_length=4000)


class AnnouncementUpdate(BaseModel):
    email_subject: str | None = Field(default=None, max_length=998)
    email_body: str | None = Field(default=None, max_length=50000)
    slack_body: str | None = Field(default=None, max_length=40000)
    notion_title: str | None = Field(default=None, max_length=300)
    notion_body: str | None = Field(default=None, max_length=80000)
    request_text: str | None = Field(default=None, max_length=4000)


class AnnouncementSchedule(BaseModel):
    scheduled_for: datetime
    recipients: list[str] = Field(default_factory=list)
    slack_channel_id: str | None = None
    notion_parent_page_id: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/admin/announcements/generate", status_code=status.HTTP_201_CREATED)
async def generate_announcement_endpoint(
    body: AnnouncementGenerate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    try:
        row = await comms_service.draft_announcement(
            org_id=org_id, user_id=user_id, request_text=body.request_text
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        log.exception("announcements.draft_failed user=%s err=%s", user_id, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't draft the announcement. Try again in a moment.",
        )
    return {"announcement": row}


@router.get("/admin/announcements")
async def list_announcements(
    current_user: dict = Depends(verify_jwt),
    limit: int = 50,
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("internal_announcements")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(min(max(limit, 1), 200))
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"announcements": rows}


@router.get("/admin/announcements/{announcement_id}")
async def get_announcement_endpoint(
    announcement_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("internal_announcements")
            .select("*")
            .eq("id", announcement_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else None) or None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Announcement not found.")
    return {"announcement": row}


@router.patch("/admin/announcements/{announcement_id}")
async def update_announcement_endpoint(
    announcement_id: str,
    body: AnnouncementUpdate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        row = await comms_service.update_announcement(
            org_id=org_id,
            user_id=user_id,
            announcement_id=announcement_id,
            fields=fields,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return {"announcement": row}


@router.post("/admin/announcements/{announcement_id}/schedule")
async def schedule_announcement_endpoint(
    announcement_id: str,
    body: AnnouncementSchedule,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    scheduled = body.scheduled_for
    # Treat naive datetimes as UTC. The frontend always sends ISO8601 with
    # timezone, but defence-in-depth.
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    if scheduled <= datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="scheduled_for must be in the future."
        )

    try:
        row = await comms_service.schedule_announcement(
            org_id=org_id,
            user_id=user_id,
            announcement_id=announcement_id,
            scheduled_for=scheduled,
            recipients=body.recipients,
            slack_channel_id=body.slack_channel_id,
            notion_parent_page_id=body.notion_parent_page_id,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return {"announcement": row}


@router.post("/admin/announcements/{announcement_id}/cancel")
async def cancel_announcement_endpoint(
    announcement_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    try:
        return await comms_service.cancel_announcement(
            org_id=org_id, user_id=user_id, announcement_id=announcement_id
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
