"""Executive Assistant endpoints (#25, Agent2 Day 6)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_user_client
from app.errors import NoOrganization
from app.models.executive_assistant import BriefingRead, GenerateBriefingRequest
from app.services import executive_assistant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/executive", tags=["executive"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


@router.post("/briefing/generate", response_model=BriefingRead)
async def generate_briefing(
    body: GenerateBriefingRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)
    try:
        row = await executive_assistant.generate_briefing(
            org_id=org_id,
            user_id=user_id,
            request_text=body.request_text,
            recipients=[str(r) for r in body.recipients],
            schedule_followup=body.schedule_followup,
            followup_proposed_start=body.followup_proposed_start,
            followup_proposed_end=body.followup_proposed_end,
            followup_title=body.followup_title,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return row


@router.get("/briefing", response_model=dict)
async def list_briefings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        res = (
            client.table("executive_briefings")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"briefings": rows}


@router.get("/briefing/{briefing_id}", response_model=BriefingRead)
async def get_briefing(
    briefing_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("executive_briefings")
            .select("*")
            .eq("id", briefing_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Briefing not found.")
    return row
