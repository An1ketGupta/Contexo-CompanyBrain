"""Recruiting Agent endpoints (#20, Agent2 Day 5).

Two POSTs, one GET listing, one GET fetch:

    POST /recruiting/requisitions/generate     create draft + 5 JD variants
    POST /recruiting/requisitions/{id}/publish publish to ATS + tracker + Slack
    GET  /recruiting/requisitions              list all in org
    GET  /recruiting/requisitions/{id}         single read

Auth:
    * Creators or admins can read everything in their org.
    * Only creator (or an admin) can publish a draft they own.
    * Generate is open to any member; the org's hiring spend gate lives in
      `usage.py` already and trips for raw chat queries — we leave it there
      and don't double-gate here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.models.recruiting import (
    GenerateRequisitionRequest,
    GenerateRequisitionResponse,
    JdVariant,
    PublishRequisitionRequest,
    RequisitionRead,
    SourcingTemplate,
)
from app.services import recruiting_agent

log = logging.getLogger(__name__)

router = APIRouter(prefix="/recruiting", tags=["recruiting"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _user_role(token: str, user_id: str) -> str | None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return (me.data or {}).get("role") if me and me.data else None


def _to_read(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a DB row into the shape RequisitionRead expects."""
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "created_by": row["created_by"],
        "role_request": row["role_request"],
        "jd_variants": [JdVariant(**v).model_dump() for v in row.get("jd_variants") or []],
        "selected_variant_index": row.get("selected_variant_index"),
        "ats_platform": row.get("ats_platform"),
        "ats_job_id": row.get("ats_job_id"),
        "ats_url": row.get("ats_url"),
        "notion_tracker_url": row.get("notion_tracker_url"),
        "sourcing_templates": [
            SourcingTemplate(**t).model_dump() for t in row.get("sourcing_templates") or []
        ],
        "linkedin_search_urls": row.get("linkedin_search_urls") or [],
        "hiring_manager_email": row.get("hiring_manager_email"),
        "slack_channel": row.get("slack_channel"),
        "status": row.get("status") or "draft",
        "error_message": row.get("error_message"),
        "created_at": row["created_at"],
        "published_at": row.get("published_at"),
    }


# ── Generate ────────────────────────────────────────────────────────────────


@router.post(
    "/requisitions/generate",
    response_model=GenerateRequisitionResponse,
)
async def generate_requisition(
    body: GenerateRequisitionRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    try:
        row = await recruiting_agent.generate_job_requisition(
            org_id=org_id,
            user_id=user_id,
            role_request=body.role_request,
            location=body.location,
            department=body.department,
        )
    except RuntimeError as exc:
        # JD synthesis problems are user-facing — we want a 502 rather than
        # a generic 500 so the UI can render "AI couldn't write a JD; try
        # rephrasing".
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return {
        "id": row["id"],
        "role_request": row["role_request"],
        "jd_variants": [JdVariant(**v).model_dump() for v in row.get("jd_variants") or []],
        "sources": row.get("sources") or [],
        "created_at": row["created_at"],
    }


# ── Publish ─────────────────────────────────────────────────────────────────


@router.post("/requisitions/{requisition_id}/publish", response_model=RequisitionRead)
async def publish_requisition(
    requisition_id: str,
    body: PublishRequisitionRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)

    # Ownership check: creator or admin only.
    svc = get_service_client()

    def _fetch_owner() -> dict[str, Any] | None:
        res = (
            svc.table("job_requisitions")
            .select("created_by, status")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    own_row = await asyncio.to_thread(_fetch_owner)
    if not own_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    if own_row["created_by"] != user_id:
        role = await _user_role(token, user_id)
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your requisition.")

    try:
        updated = await recruiting_agent.publish_requisition(
            org_id=org_id,
            user_id=user_id,
            requisition_id=requisition_id,
            selected_variant_index=body.selected_variant_index,
            ats_platform=body.ats_platform,
            hiring_manager_email=body.hiring_manager_email,
            slack_channel=body.slack_channel,
            notion_parent_page_id=body.notion_parent_page_id,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return _to_read(updated)


# ── Read ─────────────────────────────────────────────────────────────────────


@router.get("/requisitions")
async def list_requisitions(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> list[dict[str, Any]]:
        res = (
            client.table("job_requisitions")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"requisitions": [_to_read(r) for r in rows]}


@router.get("/requisitions/{requisition_id}", response_model=RequisitionRead)
async def get_requisition(
    requisition_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = get_user_client(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("job_requisitions")
            .select("*")
            .eq("id", requisition_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Requisition not found.")
    return _to_read(row)
