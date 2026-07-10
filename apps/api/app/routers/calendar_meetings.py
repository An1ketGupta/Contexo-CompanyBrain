"""Calendar Intelligence endpoints (#51, Agent2 Day 6).

Distinct from the existing /meetings router (which surfaces meeting_summaries
from MeetingNotesAgent post-meeting). This router covers the *pre-meeting*
prep brief flow built on calendar_meetings.

Endpoints:
    GET  /calendar/meetings                       upcoming meetings + brief status
    POST /calendar/meetings/sync                  trigger sync for current user
    GET  /calendar/meetings/{id}                  one meeting + prep brief
    POST /calendar/meetings/{id}/brief            generate brief on demand
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import calendar_intelligence

log = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _require_user(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found.")
    return org_id, user_id, token


# ── List + Sync ─────────────────────────────────────────────────────────────


@router.get("/meetings")
async def list_meetings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        # 7-day window — far enough to catch next week's agenda, not so far
        # that the list page bloats.
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        res = (
            client.table("calendar_meetings")
            .select("*")
            .eq("user_id", user_id)
            .gte("start_time", cutoff)
            .order("start_time", desc=False)
            .limit(100)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"meetings": rows}


@router.post("/meetings/sync")
async def sync_now(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Manually trigger a sync for the current user. The cron also runs every
    15 min, but a user clicking "Refresh" wants to see new events now."""
    org_id, user_id, _ = _require_user(current_user)
    try:
        synced = await calendar_intelligence.sync_upcoming_meetings(
            org_id=org_id, user_id=user_id
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"synced": len(synced)}


@router.get("/meetings/{meeting_id}")
async def get_meeting(
    meeting_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _org_id, user_id, token = _require_user(current_user)
    client = get_user_client(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("calendar_meetings")
            .select("*")
            .eq("id", meeting_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found.")
    return row


@router.post("/meetings/{meeting_id}/brief")
async def generate_brief(
    meeting_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Synchronously regenerate the prep brief. Useful when the cron hasn't
    fired yet or the user wants a re-run with updated KB."""
    _org_id, user_id, _ = _require_user(current_user)
    svc = get_service_client()

    # Ownership guard.
    owner = await asyncio.to_thread(
        lambda: svc.table("calendar_meetings")
        .select("user_id")
        .eq("id", meeting_id)
        .maybe_single()
        .execute()
    )
    if not owner or not owner.data or owner.data.get("user_id") != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found.")

    try:
        row = await calendar_intelligence.generate_meeting_prep_brief(
            meeting_id=meeting_id, force=True
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return row
