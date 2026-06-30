"""Marketing Agent endpoints (third agent in the family, after Sales/RFP
and Recruiting/Interview Kit).

Endpoints:
    POST   /marketing/briefs/generate    fire agent (creates brief + run)
    GET    /marketing/briefs             list briefs for the org
    GET    /marketing/briefs/{bid}       single brief
    PATCH  /marketing/briefs/{bid}       edit any artifact
    POST   /marketing/briefs/{bid}/publish    lock the brief
    DELETE /marketing/briefs/{bid}       delete (admin only)

Auth:
    Reads — any org member (sales reads positioning, recruiting reads pillars).
    Generate / edit / publish — creator or admin.
    Delete — admin only.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import inngest as _inngest_pkg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.agents.marketing_agent import storage as brief_storage
from app.services.agents.marketing_agent.schemas import (
    GenerateMarketingBriefRequest,
    MarketingBriefRead,
    UpdateMarketingBriefRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/marketing", tags=["marketing"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _user_role(token: str, user_id: str) -> str | None:
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return (res.data or {}).get("role") if res and res.data else None


def _row_to_read(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce DB row → MarketingBriefRead shape. JSONB columns are already
    parsed; stamp defaults for older / failed rows missing keys."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "run_id": row.get("run_id"),
        "objective": row.get("objective") or "",
        "audience_hint": row.get("audience_hint"),
        "channels": row.get("channels") or [],
        "competitors": row.get("competitors") or [],
        "collection_id": row.get("collection_id"),
        "positioning": row.get("positioning") or {},
        "messaging_pillars": row.get("messaging_pillars") or [],
        "competitive_angle": row.get("competitive_angle") or [],
        "channel_plan": row.get("channel_plan") or [],
        "content_brief": row.get("content_brief") or {},
        "sources": row.get("sources") or [],
        "status": row.get("status") or "draft",
        "error_message": row.get("error_message"),
        "generated_at": row.get("generated_at"),
        "published_at": row.get("published_at"),
        "created_at": row["created_at"],
        "updated_at": row.get("updated_at") or row["created_at"],
    }


# ── Generate ────────────────────────────────────────────────────────────────


@router.post("/briefs/generate", response_model=MarketingBriefRead)
async def generate_marketing_brief(
    body: GenerateMarketingBriefRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _token = _require_org(current_user)

    # Pre-mint the agent_runs id so the brief row can link to it before the
    # Inngest worker fires. MarketingAgent accepts run_id and uses it on
    # create_run_record().
    run_id = str(uuid.uuid4())

    brief_id = await brief_storage.create_brief_row(
        org_id=org_id,
        created_by=user_id,
        run_id=run_id,
        objective=body.objective.strip(),
        audience_hint=(body.audience_hint or "").strip() or None,
        channels=body.channels,
        competitors=[c.strip() for c in body.competitors if c.strip()],
        collection_id=body.collection_id,
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        _inngest_pkg.Event(
            name="marketing/brief-generate",
            data={
                "brief_id": brief_id,
                "org_id": org_id,
                "run_id": run_id,
                "objective": body.objective.strip(),
                "audience_hint": (body.audience_hint or "").strip() or None,
                "channels": body.channels,
                "competitors": [c.strip() for c in body.competitors if c.strip()],
                "collection_id": body.collection_id,
                "triggered_by_user_id": user_id,
            },
        )
    )

    row = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    if not row:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Brief row vanished after insert.",
        )
    return _row_to_read(row)


# ── List ───────────────────────────────────────────────────────────────────


@router.get("/briefs", response_model=list[MarketingBriefRead])
async def list_briefs(
    status_filter: str | None = Query(None, alias="status"),
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, Any]]:
    org_id, _user_id, _token = _require_org(current_user)
    rows = await brief_storage.list_briefs_for_org(
        org_id=org_id, status=status_filter
    )
    return [_row_to_read(r) for r in rows]


# ── Single ─────────────────────────────────────────────────────────────────


@router.get("/briefs/{brief_id}", response_model=MarketingBriefRead)
async def get_brief(
    brief_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, _token = _require_org(current_user)
    row = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Marketing brief not found.")
    return _row_to_read(row)


# ── Edit ───────────────────────────────────────────────────────────────────


@router.patch("/briefs/{brief_id}", response_model=MarketingBriefRead)
async def update_brief(
    brief_id: str,
    body: UpdateMarketingBriefRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    row = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Marketing brief not found.")

    if row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your brief.")

    if row["status"] == "generating":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Brief is still generating. Wait until it's ready before editing.",
        )

    patch: dict[str, Any] = {}
    if body.objective is not None:
        patch["objective"] = body.objective
    if body.audience_hint is not None:
        patch["audience_hint"] = body.audience_hint
    if body.positioning is not None:
        patch["positioning"] = body.positioning.model_dump()
    if body.messaging_pillars is not None:
        patch["messaging_pillars"] = [p.model_dump() for p in body.messaging_pillars]
    if body.competitive_angle is not None:
        patch["competitive_angle"] = [c.model_dump() for c in body.competitive_angle]
    if body.channel_plan is not None:
        patch["channel_plan"] = [c.model_dump() for c in body.channel_plan]
    if body.content_brief is not None:
        patch["content_brief"] = body.content_brief.model_dump()

    if not patch:
        return _row_to_read(row)

    # Editing a published brief flips it back to 'ready' so the team sees the
    # "edited after publish" state. Marketer re-publishes when satisfied.
    if row["status"] == "published":
        patch["status"] = "ready"
        patch["published_at"] = None

    await brief_storage.update_brief_row(brief_id=brief_id, patch=patch)
    refreshed = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    return _row_to_read(refreshed or row)


# ── Publish ────────────────────────────────────────────────────────────────


@router.post("/briefs/{brief_id}/publish", response_model=MarketingBriefRead)
async def publish_brief(
    brief_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    row = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Marketing brief not found.")
    if row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your brief.")
    if row["status"] not in ("ready", "published"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot publish brief with status '{row['status']}'.",
        )

    await brief_storage.update_brief_row(
        brief_id=brief_id,
        patch={
            "status": "published",
            "published_at": datetime.now(UTC).isoformat(),
        },
    )
    refreshed = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    return _row_to_read(refreshed or row)


# ── Delete ─────────────────────────────────────────────────────────────────


@router.delete("/briefs/{brief_id}")
async def delete_brief(
    brief_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, str]:
    org_id, user_id, token = _require_org(current_user)
    role = await _user_role(token, user_id)
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only.")
    row = await brief_storage.fetch_brief(org_id=org_id, brief_id=brief_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Marketing brief not found.")
    await brief_storage.delete_brief(brief_id=brief_id)
    return {"status": "deleted"}
