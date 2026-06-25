"""Admin quality metrics endpoints (#52, Agent2 Day 6).

Admin-only — RLS on message_quality_scores already restricts to admin role,
but we also gate in the router for a clean 403 instead of an empty array.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services import quality_scoring

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/quality-metrics", tags=["admin-quality"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only.")


@router.get("")
async def get_quality_metrics(
    weeks: int = Query(default=8, ge=1, le=52),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    return await quality_scoring.get_quality_trend(org_id=org_id, weeks=weeks)


class SetThresholdRequest(BaseModel):
    threshold: float | None = Field(default=None, ge=0.0, le=10.0)


@router.patch("/threshold")
async def set_threshold(
    body: SetThresholdRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _update() -> None:
        svc.table("organizations").update(
            {"quality_alert_threshold": body.threshold}
        ).eq("id", org_id).execute()

    await asyncio.to_thread(_update)
    return {"threshold": body.threshold}


@router.post("/backfill")
async def backfill(
    days: int = Query(default=30, ge=1, le=180),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """One-shot: compute scores for the last N days of messages so the trend
    chart isn't empty on first load post-deploy. Safe to re-run (upserts)."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    return await quality_scoring.backfill_org_scores(org_id=org_id, days=days)
