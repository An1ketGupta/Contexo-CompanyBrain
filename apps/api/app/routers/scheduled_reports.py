"""Scheduled reports CRUD (V5 #98).

Admin-only writes; org members can list. RLS on `scheduled_reports` enforces
both tenant isolation and the admin-only mutate rules at the DB layer; the
router checks for ergonomic 403s instead of letting RLS surface as a 500.

Routes:
    GET    /scheduled-reports                — list all reports for current org
    POST   /scheduled-reports                — create a new schedule
    PATCH  /scheduled-reports/{id}           — update an existing schedule
    DELETE /scheduled-reports/{id}           — remove a schedule
    POST   /scheduled-reports/{id}/send-now  — fire the report immediately
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.report_scheduler import compute_next_send_at

import inngest

log = get_logger(__name__)

router = APIRouter(prefix="/scheduled-reports", tags=["scheduled-reports"])

ReportType = Literal["usage_summary", "knowledge_health"]
Frequency = Literal["daily", "weekly", "monthly"]


# ── Pydantic models ─────────────────────────────────────────────────────────


# Loose email regex — the DB CHECK + Resend's own validation are the final
# guardrails. Pydantic's EmailStr would require email-validator as a runtime
# dep; not worth pulling in for an admin-only form.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _validate_emails(v: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in v or []:
        addr = (raw or "").strip()
        if not addr or not _EMAIL_RE.match(addr):
            raise ValueError(f"Invalid email address: {raw!r}")
        if addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append(addr)
    if not out:
        raise ValueError("At least one recipient required")
    if len(out) > 20:
        raise ValueError("Maximum 20 recipients per report")
    return out


class ScheduledReportBody(BaseModel):
    """Shared shape for POST + PATCH. PATCH treats all fields as optional via
    a separate class below — keeping them required here makes POST validation
    self-documenting."""

    frequency: Frequency
    report_type: ReportType
    recipients: list[str] = Field(min_length=1, max_length=20)
    send_time_utc: int = Field(default=8, ge=0, le=23)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    is_active: bool = True

    @field_validator("recipients")
    @classmethod
    def _validate_recipients(cls, v: list[str]) -> list[str]:
        return _validate_emails(v)


class ScheduledReportPatch(BaseModel):
    frequency: Frequency | None = None
    report_type: ReportType | None = None
    recipients: list[str] | None = Field(default=None, min_length=1, max_length=20)
    send_time_utc: int | None = Field(default=None, ge=0, le=23)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    is_active: bool | None = None

    @field_validator("recipients")
    @classmethod
    def _validate_recipients(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        return _validate_emails(v)


# ── Helpers ────────────────────────────────────────────────────────────────


async def _require_admin(current_user: dict) -> tuple[str, str]:
    """Returns (org_id, user_id). Raises 403 if not admin, 400 if no org."""
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise NoOrganization("No organization found.")
    user_client = get_user_client(current_user["token"])
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only.",
        )
    return org_id, user_id


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    """Pass-through serializer — we expose the exact row shape the table has,
    so we don't need an explicit response model and the frontend can rely on
    Pydantic-validated input types for symmetry."""
    return {
        "id": row.get("id"),
        "org_id": row.get("org_id"),
        "created_by": row.get("created_by"),
        "report_type": row.get("report_type"),
        "frequency": row.get("frequency"),
        "recipients": row.get("recipients") or [],
        "send_time_utc": row.get("send_time_utc"),
        "day_of_week": row.get("day_of_week"),
        "day_of_month": row.get("day_of_month"),
        "is_active": bool(row.get("is_active")),
        "last_sent_at": row.get("last_sent_at"),
        "next_send_at": row.get("next_send_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_reports(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """All scheduled reports for the caller's org (RLS-scoped)."""
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found.")
    client = get_user_client(current_user["token"])
    res = await asyncio.to_thread(
        lambda: client.table("scheduled_reports")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return {"reports": [_serialize(r) for r in (res.data or [])]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ScheduledReportBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = await _require_admin(current_user)

    next_send = compute_next_send_at(
        frequency=body.frequency,
        send_time_utc=body.send_time_utc,
        day_of_week=body.day_of_week,
        day_of_month=body.day_of_month,
    )

    row = {
        "org_id": org_id,
        "created_by": user_id,
        "recipients": body.recipients,
        "frequency": body.frequency,
        "day_of_week": body.day_of_week,
        "day_of_month": body.day_of_month,
        "send_time_utc": body.send_time_utc,
        "report_type": body.report_type,
        "is_active": body.is_active,
        "next_send_at": next_send.isoformat(),
    }
    # Writes via service-role to bypass the RLS WITH-CHECK (which we already
    # equivalently enforce via _require_admin). This keeps the row shape free
    # of trigger surprises if the policy is later tightened.
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("scheduled_reports").insert(row).execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create report.",
        )
    return _serialize(res.data[0])


@router.patch("/{report_id}")
async def update_report(
    report_id: str,
    body: ScheduledReportPatch,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _ = await _require_admin(current_user)
    _validate_uuid(report_id)

    svc = get_service_client()
    existing = await asyncio.to_thread(
        lambda: svc.table("scheduled_reports")
        .select("*")
        .eq("id", report_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not existing or not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    current = existing.data
    patch: dict[str, Any] = {}
    schedule_changed = False
    for field, value in body.model_dump(exclude_unset=True).items():
        # `value is None` is meaningful for nullable schedule fields (e.g.
        # clearing day_of_week when switching from weekly → daily) so we
        # keep it. The non-nullable validators above guarantee recipients
        # is non-empty when supplied.
        patch[field] = value
        if field in {"frequency", "send_time_utc", "day_of_week", "day_of_month"}:
            schedule_changed = True

    if schedule_changed or "is_active" in patch:
        merged = {**current, **patch}
        if merged.get("is_active"):
            patch["next_send_at"] = compute_next_send_at(
                frequency=merged["frequency"],
                send_time_utc=int(merged.get("send_time_utc") or 8),
                day_of_week=merged.get("day_of_week"),
                day_of_month=merged.get("day_of_month"),
            ).isoformat()

    if not patch:
        return _serialize(current)

    res = await asyncio.to_thread(
        lambda: svc.table("scheduled_reports")
        .update(patch)
        .eq("id", report_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update report.",
        )
    return _serialize(res.data[0])


@router.delete("/{report_id}")
async def delete_report(
    report_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Hard-delete a scheduled report. Idempotent — deleting a non-existent
    row returns {"deleted": False} rather than 404 so the UI's optimistic
    refresh doesn't show a confusing error after a successful local removal."""
    org_id, _ = await _require_admin(current_user)
    _validate_uuid(report_id)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("scheduled_reports")
        .delete()
        .eq("id", report_id)
        .eq("org_id", org_id)
        .execute()
    )
    return {"deleted": bool(res.data)}


@router.post("/{report_id}/send-now")
async def send_now(
    report_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Fire the report immediately via Inngest. Useful for testing a new
    schedule's recipients + content. Doesn't shift next_send_at — that stays
    on its normal cadence."""
    org_id, _ = await _require_admin(current_user)
    _validate_uuid(report_id)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("scheduled_reports")
        .select("*")
        .eq("id", report_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="reports/dispatch-one",
            data={"report_id": report_id, "force": True},
        )
    )
    return {"status": "queued"}


# ── UUID guard ──────────────────────────────────────────────────────────────


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _validate_uuid(value: str) -> None:
    """FastAPI accepts strings happily; supabase raises 22P02 with a chatty
    error if the id isn't a UUID. Pre-validate so we 400 cleanly."""
    if not _UUID_RE.match(value or ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid id.")
