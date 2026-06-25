"""Proactive Morning Briefings (Feature 2.2).

Endpoints:
  GET    /briefings                       — list the caller's briefings (most recent first)
  GET    /briefings/{id}                  — fetch one briefing's body
  GET    /briefings/preferences           — read caller's prefs (auto-create defaults)
  PATCH  /briefings/preferences           — update prefs
  POST   /briefings/run-now               — manually trigger this week's briefing

All endpoints are user-scoped via RLS; no admin layer.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/briefings", tags=["briefings"])


def _require_user(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )
    return org_id, user_id, token


class PreferencesUpdate(BaseModel):
    enabled: bool | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int | None = Field(default=None, ge=0, le=23)
    timezone: str | None = Field(default=None, max_length=80)
    via_email: bool | None = None
    via_inapp: bool | None = None
    topics: list[str] | None = Field(default=None, max_length=10)


@router.get("")
async def list_briefings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, list[dict[str, Any]]]:
    _, user_id, token = _require_user(current_user)
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("briefings")
        .select("id, status, summary, period_key, created_at, delivered_email_at, delivered_inapp_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )
    return {"briefings": list(getattr(res, "data", None) or [])}


@router.get("/preferences")
async def get_preferences(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    client = get_user_client(token)

    res = await asyncio.to_thread(
        lambda: client.table("briefing_preferences")
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    row = (getattr(res, "data", None) or None) if res else None
    if row:
        return {"preferences": row}

    # Auto-provision defaults on first read so the settings UI has something
    # to bind to. We use the user-scoped client so the RLS WITH CHECK on
    # (org_id, user_id) validates correctly.
    payload = {
        "user_id": user_id,
        "org_id": org_id,
    }
    ins = await asyncio.to_thread(
        lambda: client.table("briefing_preferences")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    return {"preferences": (ins.data or [payload])[0]}


@router.patch("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    client = get_user_client(token)

    update: dict[str, Any] = {}
    if body.enabled is not None:
        update["enabled"] = body.enabled
    if body.weekday is not None:
        update["weekday"] = body.weekday
    if body.hour is not None:
        update["hour"] = body.hour
    if body.timezone is not None:
        update["timezone"] = body.timezone.strip() or "UTC"
    if body.via_email is not None:
        update["via_email"] = body.via_email
    if body.via_inapp is not None:
        update["via_inapp"] = body.via_inapp
    if body.topics is not None:
        # Lowercase + dedupe + cap each entry — these go straight to the
        # LLM prompt context.
        topics = [t.strip().lower()[:80] for t in body.topics if t and t.strip()]
        update["topics"] = list(dict.fromkeys(topics))[:10]

    if not update:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No changes supplied."
        )

    # UPSERT — covers users who never read prefs before they wrote them.
    payload = {**update, "user_id": user_id, "org_id": org_id}
    res = await asyncio.to_thread(
        lambda: client.table("briefing_preferences")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    return {"preferences": (res.data or [payload])[0]}


@router.get("/{briefing_id}")
async def get_briefing(
    briefing_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, _user_id, token = _require_user(current_user)
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("briefings")
        .select("id, status, error_message, summary, body_md, data, period_key, created_at")
        .eq("id", briefing_id)
        .maybe_single()
        .execute()
    )
    row = (getattr(res, "data", None) or None) if res else None
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Briefing not found."
        )
    return {"briefing": row}


@router.post("/run-now")
async def run_now(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Queue an out-of-band generation for this user's current week.

    Useful for first-run "show me what this looks like" or after the user
    flips on the toggle and doesn't want to wait until Monday.
    """
    org_id, user_id, _token = _require_user(current_user)
    try:
        import inngest as _inngest

        from app.inngest.client import get_inngest_client
        from app.services.briefings import current_period_key

        client = get_inngest_client()
        period_key = current_period_key()
        await asyncio.to_thread(
            lambda: client.send(
                _inngest.Event(
                    name="briefings/deliver",
                    data={
                        "org_id": org_id,
                        "user_id": user_id,
                        "period_key": period_key,
                        "trigger": "manual",
                    },
                )
            )
        )
    except Exception as exc:
        log.warning("briefing_manual_trigger_failed user=%s err=%s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not queue briefing. Try again in a minute.",
        ) from exc
    return {"queued": True}
