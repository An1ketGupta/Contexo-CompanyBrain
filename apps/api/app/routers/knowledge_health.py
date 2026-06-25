"""Knowledge Health endpoints — admin-only (Agent2 Day 3 #24).

Three surfaces:
  * GET  /admin/knowledge-health/latest          — most recent report
  * GET  /admin/knowledge-health/history         — paginated list of reports
  * POST /admin/knowledge-health/scan            — enqueue an ad-hoc scan
  * GET  /admin/knowledge-health/settings        — read curator config
  * PATCH /admin/knowledge-health/settings       — update curator config

Reads service-role; admin role check is enforced in-handler so the
caller's JWT doesn't need to carry an admin flag (we just look up the
user's role each request — same pattern as routers/admin_intake.py).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge-health"])


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


class KnowledgeHealthReportSummary(BaseModel):
    id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    triggered_by: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    error_message: str | None = None


class KnowledgeHealthReportDetail(BaseModel):
    id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    triggered_by: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class CuratorSettings(BaseModel):
    curator_enabled: bool
    curator_outdated_days: int = Field(ge=30, le=3650)
    curator_check_broken_links: bool
    curator_merge_threshold: float = Field(ge=0.5, le=0.99)


class CuratorSettingsUpdate(BaseModel):
    curator_enabled: bool | None = None
    curator_outdated_days: int | None = Field(default=None, ge=30, le=3650)
    curator_check_broken_links: bool | None = None
    curator_merge_threshold: float | None = Field(default=None, ge=0.5, le=0.99)


# ── Read endpoints ───────────────────────────────────────────────────────


@router.get("/admin/knowledge-health/latest")
async def get_latest_report(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Most recent report for this org (any status — includes a running scan)."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("knowledge_health_reports")
            .select("id, status, started_at, completed_at, triggered_by, payload, error_message")
            .eq("org_id", org_id)
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        return {"report": None}
    return {"report": row}


@router.get("/admin/knowledge-health/history")
async def get_report_history(
    current_user: dict = Depends(verify_jwt),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Paginated list of completed/failed reports for this org."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("knowledge_health_reports")
            .select(
                "id, status, started_at, completed_at, triggered_by, "
                "payload->counts as counts, error_message"
            )
            .eq("org_id", org_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    return {"reports": rows}


@router.get("/admin/knowledge-health/reports/{report_id}")
async def get_report(
    report_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Full report payload by id."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("knowledge_health_reports")
            .select("id, status, started_at, completed_at, triggered_by, payload, error_message")
            .eq("org_id", org_id)
            .eq("id", report_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else None) or None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report not found.")
    return {"report": row}


# ── Scan trigger ─────────────────────────────────────────────────────────


@router.post("/admin/knowledge-health/scan", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scan(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Enqueue an ad-hoc Curator scan.

    The Inngest function is debounced 5 minutes per org so spamming this
    button doesn't fan out multiple scans. Returns 202 immediately; the
    admin watches the report list for the new row to appear.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="knowledge/curator.scan",
            data={
                "org_id": org_id,
                "triggered_by": user_id,
                "trigger": "manual",
            },
        )
    )
    return {"status": "queued"}


# ── Settings ─────────────────────────────────────────────────────────────


@router.get("/admin/knowledge-health/settings")
async def get_settings_endpoint(
    current_user: dict = Depends(verify_jwt),
) -> CuratorSettings:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any]:
        res = (
            svc.table("organizations")
            .select(
                "curator_enabled, curator_outdated_days, "
                "curator_check_broken_links, curator_merge_threshold"
            )
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else {}) or {}

    row = await asyncio.to_thread(_fetch)
    return CuratorSettings(
        curator_enabled=bool(row.get("curator_enabled", True)),
        curator_outdated_days=int(row.get("curator_outdated_days", 540)),
        curator_check_broken_links=bool(row.get("curator_check_broken_links", True)),
        curator_merge_threshold=float(row.get("curator_merge_threshold", 0.75)),
    )


@router.patch("/admin/knowledge-health/settings")
async def update_settings_endpoint(
    body: CuratorSettingsUpdate,
    current_user: dict = Depends(verify_jwt),
) -> CuratorSettings:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        # No-op patch — return current state.
        return await get_settings_endpoint(current_user=current_user)  # type: ignore[arg-type]

    def _update() -> None:
        svc.table("organizations").update(updates).eq("id", org_id).execute()

    await asyncio.to_thread(_update)
    return await get_settings_endpoint(current_user=current_user)  # type: ignore[arg-type]
