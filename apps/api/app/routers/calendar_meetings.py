"""Calendar Intelligence endpoints (#51, Agent2 Day 6).

Distinct from the existing /meetings router (which surfaces meeting_summaries
from MeetingNotesAgent post-meeting). This router covers the *pre-meeting*
prep brief flow built on calendar_meetings.

Endpoints:
    GET  /calendar/meetings                       upcoming meetings + brief status
    POST /calendar/meetings/sync                  trigger sync for current user
    GET  /calendar/meetings/{id}                  one meeting + prep brief
    POST /calendar/meetings/{id}/brief            generate brief on demand
    POST /calendar/meetings/{id}/share            email the brief to attendees
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import calendar_intelligence
from app.services.integrations import gmail as gmail_svc
from app.services.integrations import google_workspace

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
        row = await calendar_intelligence.generate_meeting_prep_brief(meeting_id=meeting_id)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return row


# ── Share brief with attendees ──────────────────────────────────────────────


class ShareBriefRequest(BaseModel):
    additional_message: str | None = None


@router.post("/meetings/{meeting_id}/share")
async def share_brief(
    meeting_id: str,
    body: ShareBriefRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Email the prep brief to every attendee on the meeting.

    Send-from is the user's Gmail (per-user OAuth). We do NOT bcc external
    recipients — calendar invites already exposed everyone's email to
    everyone else, so plain `To:` is appropriate.
    """
    org_id, user_id, _ = _require_user(current_user)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("calendar_meetings")
            .select("*")
            .eq("id", meeting_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    meeting = await asyncio.to_thread(_fetch)
    if not meeting:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found.")
    if meeting.get("prep_brief_status") != "ready" or not meeting.get("prep_brief"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Brief not ready.")

    creds = await gmail_svc.get_user_credentials(org_id=org_id, user_id=user_id)
    if not creds:
        token = await google_workspace.get_user_token(org_id=org_id, user_id=user_id)
        sender = await google_workspace.get_user_email(org_id=org_id, user_id=user_id)
        if not (token and sender):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Connect Gmail or Google Workspace to share briefs.",
            )
        access_token = token
    else:
        access_token = creds["access_token"]
        sender = creds["email_address"]

    pb = meeting["prep_brief"]
    title = meeting.get("title") or "Upcoming meeting"
    body_lines = [
        f"Prep brief for: {title}",
        f"Time: {meeting.get('start_time')}",
        "",
        f"## Summary\n{pb.get('executive_summary') or '_(no summary)_'}",
        "",
        f"## Topic research\n{pb.get('topic_research') or '_(no research)_'}",
        "",
        "## Suggested questions",
    ]
    for q in pb.get("suggested_questions") or []:
        body_lines.append(f"- {q}")
    if body.additional_message:
        body_lines.append("")
        body_lines.append(body.additional_message)
    body_text = "\n".join(body_lines)

    sent_to: list[str] = []
    for r in meeting.get("attendee_emails") or []:
        try:
            await gmail_svc.send_email(
                access_token=access_token,
                sender=sender or "noreply@nirnayaiq.local",
                to=r,
                subject=f"Prep: {title}",
                body=body_text,
            )
            sent_to.append(r)
        except Exception as exc:
            log.warning(
                "calendar.share.send_failed user=%s to=%s err=%s", user_id, r, exc
            )
    return {"sent_to": sent_to, "skipped": len((meeting.get("attendee_emails") or [])) - len(sent_to)}
