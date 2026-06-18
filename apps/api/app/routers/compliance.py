"""Compliance acknowledgement APIs (Agent Roadmap Day 10).

Endpoints:
    GET  /compliance/my-pending                       — user's pending acks
    GET  /compliance/my-acknowledged                  — user's history
    POST /compliance/{document_id}/acknowledge        — user accepts current version
    POST /compliance/{document_id}/dismiss            — user dismisses (admin can audit)

Admin:
    GET  /compliance/admin/report                     — per-document + per-user roll-up
    GET  /compliance/admin/report.csv                 — same data, CSV download
    POST /compliance/admin/{doc_id}/repropagate       — manually re-run the policy agent
    POST /compliance/admin/{doc_id}/remind-now        — send reminder emails immediately
    GET  /compliance/admin/config                     — current org compliance config
    PATCH /compliance/admin/config                    — update reminder cadence + channel

Auth model:
    User-scoped client for the per-user views (RLS already restricts to own
    rows + admins org-wide). Service client for the admin roll-up because
    we cross auth.users for emails.

The acknowledgement model is always "latest version" — when a doc gets a
new version_id, fresh pending rows fan out from the propagation agent.
Re-acknowledging is a normal expectation; the UI surfaces the diff so the
user knows what they're re-acknowledging.
"""
from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.org_config import invalidate as invalidate_org_config
from app.services.webhooks import trigger_event as trigger_webhook_event

log = get_logger(__name__)

router = APIRouter(prefix="/compliance", tags=["compliance"])


# ── Models ──────────────────────────────────────────────────────────────────


class ComplianceConfigPayload(BaseModel):
    reminder_threshold_days: int = Field(default=3, ge=1, le=30)
    max_reminders: int = Field(default=3, ge=0, le=10)
    reminder_cadence_days: int = Field(default=1, ge=1, le=14)
    policy_channel_id: str | None = Field(default=None, max_length=64)
    auto_propagate_policy_tag: bool = True


# ── Helpers ─────────────────────────────────────────────────────────────────


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _require_admin(current_user: dict) -> tuple[str, str, str]:
    user_id, org_id, token = _require_user(current_user)
    user_client = get_user_client(token)
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
            detail="Admin role required.",
        )
    return user_id, org_id, token


def _require_uuid(value: str, field: str = "id") -> None:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field}.",
        ) from exc


# ── User endpoints ──────────────────────────────────────────────────────────


@router.get("/my-pending")
async def list_my_pending(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Documents this user must acknowledge.

    We join the diff summary so the banner / pending page can show "what
    changed" without a second round-trip. Diff is optional — first-time
    policies have no prior version.
    """
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("acknowledgements")
        .select(
            "id, document_id, document_version_id, status, created_at, "
            "diff_id, "
            "documents(id, name, file_type, tags, metadata), "
            "document_versions(id, version_number, created_at), "
            "document_diffs(id, diff_summary, from_version, to_version)"
        )
        .eq("user_id", user_id)
        .eq("status", "pending")
        .order("created_at", desc=False)
        .execute()
    )
    return {"pending": res.data or []}


@router.get("/my-acknowledged")
async def list_my_acknowledged(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("acknowledgements")
        .select(
            "id, document_id, document_version_id, acknowledged_at, "
            "documents(id, name, file_type), "
            "document_versions(version_number)"
        )
        .eq("user_id", user_id)
        .eq("status", "acknowledged")
        .order("acknowledged_at", desc=True)
        .limit(50)
        .execute()
    )
    return {"acknowledged": res.data or []}


@router.post("/{document_id}/acknowledge")
async def acknowledge_document(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _require_uuid(document_id, "document_id")
    user_id, org_id, token = _require_user(current_user)

    client = get_user_client(token)
    # Acknowledge ALL pending rows for this user+doc. In practice there's
    # exactly one pending per (user, current version), but a no-op extra
    # update doesn't hurt and protects against weird historical states.
    now = datetime.now(timezone.utc).isoformat()
    res = await asyncio.to_thread(
        lambda: client.table("acknowledgements")
        .update({"status": "acknowledged", "acknowledged_at": now})
        .eq("document_id", document_id)
        .eq("user_id", user_id)
        .eq("status", "pending")
        .execute()
    )
    affected = len(res.data or [])
    if affected == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending acknowledgement for this document.",
        )

    # Outbound webhook — typically `rows == 1`, but the update is multi-row
    # tolerant so we mirror that in the payload. The version id lets a
    # receiver tell "first ack vs. re-ack of a new version" apart.
    try:
        first_row = (res.data or [{}])[0]
        await trigger_webhook_event(
            org_id=org_id,
            event="compliance.acknowledged",
            payload={
                "document_id": document_id,
                "document_version_id": first_row.get("document_version_id"),
                "user_id": user_id,
                "acknowledged_at": now,
                "rows": affected,
            },
        )
    except Exception as exc:
        log.warning(
            "compliance_acknowledged_webhook_failed",
            document_id=document_id,
            user_id=user_id,
            error=str(exc),
        )

    return {"acknowledged": True, "rows": affected}


@router.post("/{document_id}/dismiss")
async def dismiss_acknowledgement(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """User dismisses without acknowledging — surfaces on the admin report
    as a 'dismissed' bucket. Used when a doc doesn't apply to them; admin
    follows up rather than chasing forever."""
    _require_uuid(document_id, "document_id")
    user_id, _, token = _require_user(current_user)
    client = get_user_client(token)
    await asyncio.to_thread(
        lambda: client.table("acknowledgements")
        .update({"status": "dismissed"})
        .eq("document_id", document_id)
        .eq("user_id", user_id)
        .eq("status", "pending")
        .execute()
    )
    return {"dismissed": True}


# ── Admin: roll-up report ───────────────────────────────────────────────────


@router.get("/admin/report")
async def compliance_report(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Two views in one payload — per-document and per-user.

    We compute everything in-process from a single acknowledgements pull
    rather than per-doc queries. At realistic org sizes (hundreds of users
    × tens of policy docs) this is well under 50k rows, so a single select
    + group-by in Python is faster than N+1 round trips.
    """
    _user_id, org_id, _token = await _require_admin(current_user)
    svc = get_service_client()

    acks_res = await asyncio.to_thread(
        lambda: svc.table("acknowledgements")
        .select(
            "id, document_id, document_version_id, user_id, status, "
            "acknowledged_at, reminder_count, created_at, "
            "documents(id, name, file_type, requires_acknowledgement, current_version_id), "
            "document_versions(version_number, created_at), "
            "users(id, display_name)"
        )
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(20000)
        .execute()
    )
    rows = acks_res.data or []

    by_doc: dict[str, dict[str, Any]] = {}
    by_user: dict[str, dict[str, Any]] = {}

    for r in rows:
        doc = (r.get("documents") or {})
        version = (r.get("document_versions") or {})
        doc_id = r.get("document_id")
        user_id = r.get("user_id")
        if not doc_id or not user_id:
            continue

        d = by_doc.setdefault(
            doc_id,
            {
                "document_id": doc_id,
                "name": doc.get("name"),
                "file_type": doc.get("file_type"),
                "version_number": version.get("version_number"),
                "total": 0,
                "acknowledged": 0,
                "pending": 0,
                "dismissed": 0,
                "last_updated": version.get("created_at") or r.get("created_at"),
            },
        )
        d["total"] += 1
        if r.get("status") == "acknowledged":
            d["acknowledged"] += 1
        elif r.get("status") == "dismissed":
            d["dismissed"] += 1
        else:
            d["pending"] += 1

        u = by_user.setdefault(
            user_id,
            {
                "user_id": user_id,
                "display_name": (r.get("users") or {}).get("display_name"),
                "total": 0,
                "acknowledged": 0,
                "pending": 0,
                "dismissed": 0,
                "last_acknowledged_at": None,
                "pending_docs": [],
            },
        )
        u["total"] += 1
        if r.get("status") == "acknowledged":
            u["acknowledged"] += 1
            ack_at = r.get("acknowledged_at")
            if ack_at and (u["last_acknowledged_at"] is None or ack_at > u["last_acknowledged_at"]):
                u["last_acknowledged_at"] = ack_at
        elif r.get("status") == "dismissed":
            u["dismissed"] += 1
        else:
            u["pending"] += 1
            u["pending_docs"].append({"document_id": doc_id, "name": doc.get("name")})

    # Hydrate user emails via auth admin (service-role only).
    user_ids = list(by_user.keys())
    emails_by_id: dict[str, str] = {}
    if user_ids:
        for uid in user_ids:
            try:
                au = await asyncio.to_thread(lambda u=uid: svc.auth.admin.get_user_by_id(u))
                email = getattr(getattr(au, "user", None), "email", None)
                if email:
                    emails_by_id[uid] = email
            except Exception:
                continue
    for uid, u in by_user.items():
        u["email"] = emails_by_id.get(uid)
        # Sort pending docs newest-first; cap to keep payload bounded.
        u["pending_docs"] = u["pending_docs"][:25]

    def _pct(num: int, denom: int) -> float:
        return round((num / denom) * 100, 1) if denom else 0.0

    documents = [
        {**d, "completion_pct": _pct(d["acknowledged"], d["total"])}
        for d in by_doc.values()
    ]
    documents.sort(key=lambda x: (x["completion_pct"], x["name"] or ""))

    users = list(by_user.values())
    users.sort(key=lambda x: (x["pending"], x.get("display_name") or x.get("email") or ""), reverse=True)

    summary = {
        "total_acks": sum(d["total"] for d in documents),
        "acknowledged": sum(d["acknowledged"] for d in documents),
        "pending": sum(d["pending"] for d in documents),
        "dismissed": sum(d["dismissed"] for d in documents),
        "policy_documents": len(documents),
        "users_with_pending": sum(1 for u in users if u["pending"] > 0),
    }
    summary["overall_completion_pct"] = _pct(summary["acknowledged"], summary["total_acks"])

    return {"summary": summary, "by_document": documents, "by_user": users}


@router.get("/admin/report.csv")
async def compliance_report_csv(current_user: dict = Depends(verify_jwt)) -> Response:
    """Same data as /admin/report flattened to CSV.

    Streams a single Document × User matrix so the admin can hand it to
    People Ops / Legal without further processing.
    """
    _user_id, org_id, _token = await _require_admin(current_user)
    svc = get_service_client()

    rows_res = await asyncio.to_thread(
        lambda: svc.table("acknowledgements")
        .select(
            "id, status, acknowledged_at, created_at, reminder_count, "
            "documents(name, file_type), "
            "document_versions(version_number), "
            "users(id, display_name)"
        )
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(50000)
        .execute()
    )
    rows = rows_res.data or []

    user_ids = list({(r.get("users") or {}).get("id") for r in rows if (r.get("users") or {}).get("id")})
    emails_by_id: dict[str, str] = {}
    for uid in user_ids:
        try:
            au = await asyncio.to_thread(lambda u=uid: svc.auth.admin.get_user_by_id(u))
            email = getattr(getattr(au, "user", None), "email", None)
            if email:
                emails_by_id[uid] = email
        except Exception:
            continue

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "document_name",
        "file_type",
        "version",
        "user_name",
        "user_email",
        "status",
        "acknowledged_at",
        "created_at",
        "reminders_sent",
    ])
    for r in rows:
        doc = r.get("documents") or {}
        version = r.get("document_versions") or {}
        user = r.get("users") or {}
        writer.writerow([
            doc.get("name") or "",
            doc.get("file_type") or "",
            version.get("version_number") or "",
            user.get("display_name") or "",
            emails_by_id.get(user.get("id") or "", ""),
            r.get("status") or "",
            r.get("acknowledged_at") or "",
            r.get("created_at") or "",
            r.get("reminder_count") or 0,
        ])

    csv_bytes = buf.getvalue().encode("utf-8")
    filename = f"compliance-report-{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Admin: re-propagate + remind-now ────────────────────────────────────────


@router.post("/admin/{document_id}/repropagate")
async def admin_repropagate(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Re-run the propagation agent against the document's current version.

    Use cases:
      * The Slack channel was misconfigured the first time.
      * Admin added members after the original propagation and wants fresh
        ack rows for them (the agent's upsert is idempotent for existing
        users — only new users get pending rows).
    """
    _require_uuid(document_id, "document_id")
    _, org_id, _ = await _require_admin(current_user)

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="agent/policy-propagate",
            data={
                "document_id": document_id,
                "org_id": org_id,
                "triggered_by": "manual",
            },
            id=f"policy-repropagate-{document_id}-{uuid.uuid4().hex[:8]}",
        )
    )
    return {"queued": True}


@router.post("/admin/{document_id}/remind-now")
async def admin_remind_now(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Force an immediate reminder email to all users with this doc pending.

    Bypasses the threshold-days gate (the daily cron honours it). Useful
    after a launch-day mistake or a compliance deadline. Honours the
    max_reminders cap so a panicked admin can't spam the team.
    """
    _require_uuid(document_id, "document_id")
    _, org_id, _ = await _require_admin(current_user)

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="compliance/reminder-now",
            data={"org_id": org_id, "document_id": document_id},
            id=f"compliance-remind-now-{document_id}-{uuid.uuid4().hex[:8]}",
        )
    )
    return {"queued": True}


# ── Admin: per-org compliance config ────────────────────────────────────────


@router.get("/admin/config")
async def get_compliance_config(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    _, org_id, _ = await _require_admin(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    meta = ((res.data or {}).get("metadata") or {}) if res else {}
    compliance = meta.get("compliance") or {}
    return {
        "reminder_threshold_days": int(compliance.get("reminder_threshold_days") or 3),
        "max_reminders": int(compliance.get("max_reminders") or 3),
        "reminder_cadence_days": int(compliance.get("reminder_cadence_days") or 1),
        "policy_channel_id": compliance.get("policy_channel_id"),
        "auto_propagate_policy_tag": bool(compliance.get("auto_propagate_policy_tag", True)),
    }


@router.patch("/admin/config")
async def update_compliance_config(
    payload: ComplianceConfigPayload,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _, org_id, _ = await _require_admin(current_user)
    svc = get_service_client()

    def _apply() -> dict[str, Any]:
        existing = (
            svc.table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        meta = ((existing.data or {}).get("metadata") or {}) if existing else {}
        meta = {
            **meta,
            "compliance": {
                "reminder_threshold_days": payload.reminder_threshold_days,
                "max_reminders": payload.max_reminders,
                "reminder_cadence_days": payload.reminder_cadence_days,
                "policy_channel_id": (payload.policy_channel_id or "").strip() or None,
                "auto_propagate_policy_tag": payload.auto_propagate_policy_tag,
            },
        }
        svc.table("organizations").update({"metadata": meta}).eq("id", org_id).execute()
        return meta["compliance"]

    compliance = await asyncio.to_thread(_apply)
    invalidate_org_config(org_id)
    return compliance


# ── Document-detail: per-doc ack roster (admin view) ────────────────────────


@router.get("/admin/document/{document_id}")
async def admin_document_status(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _require_uuid(document_id, "document_id")
    _, org_id, _ = await _require_admin(current_user)

    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("acknowledgements")
        .select(
            "id, status, acknowledged_at, reminder_count, last_reminder_at, "
            "created_at, user_id, "
            "users(display_name)"
        )
        .eq("org_id", org_id)
        .eq("document_id", document_id)
        .order("status")
        .execute()
    )
    rows = res.data or []

    user_ids = [r["user_id"] for r in rows if r.get("user_id")]
    emails: dict[str, str] = {}
    for uid in user_ids:
        try:
            au = await asyncio.to_thread(lambda u=uid: svc.auth.admin.get_user_by_id(u))
            email = getattr(getattr(au, "user", None), "email", None)
            if email:
                emails[uid] = email
        except Exception:
            continue

    return {
        "roster": [
            {
                **r,
                "user_email": emails.get(r.get("user_id") or ""),
                "user_name": (r.get("users") or {}).get("display_name"),
            }
            for r in rows
        ]
    }
