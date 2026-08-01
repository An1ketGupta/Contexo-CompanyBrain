"""Admin surface for the sales outreach agent.

  GET  /admin/sales                            — list leads, filter by status
  POST /admin/sales/import                     — bulk-import leads
  GET  /admin/sales/settings                   — org trust-mode config
  PUT  /admin/sales/settings                   — update org trust-mode config
  GET  /admin/sales/settings/senders           — connectable Gmail mailboxes
  GET  /admin/sales/{lead_id}                  — lead + message thread
  POST /admin/sales/{lead_id}/send             — approve (possibly edited) draft
  POST /admin/sales/{lead_id}/reject           — reject a draft
  POST /admin/sales/{lead_id}/reply            — log a reply the prospect sent
  POST /admin/sales/{lead_id}/regenerate       — re-run the drafting step

All reads/writes go through the user-scoped client (`get_user_client`), never
`get_service_client`, so Postgres RLS — not this router — is what actually
enforces org isolation and the admin-only gate. `_require_admin` is
belt-and-suspenders on top, matching every other admin router here.

The send endpoint is the human half of the approval gate: it, not the agent,
is how the overwhelming majority of sales mail actually leaves. It shares its
mailbox resolution and follow-up scheduling with the agent's autonomous path
(`app.services.sales_outreach`) so approved and autonomous sends can't drift.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.auth import verify_jwt
from app.database import get_user_client
from app.inngest import get_inngest_client
from app.observability import get_logger
from app.services import sales_outreach as so
from app.services.integrations import gmail

log = get_logger(__name__)

router = APIRouter(prefix="/admin/sales", tags=["admin-sales"])

_VALID_STATUSES = {
    "new", "shadow_drafted", "pending_review", "awaiting_reply", "replied",
    "qualified", "meeting_booked", "escalated", "won", "lost",
}

# One import request. Large enough for a realistic conference lead list,
# small enough that the fan-out of Inngest events stays sane.
_MAX_IMPORT_ROWS = 500


async def _require_admin(current_user: dict) -> tuple[str, Any]:
    """Returns (org_id, user_client). Raises 403 if the caller isn't an org admin."""
    org_id = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization found.")
    user_client = get_user_client(current_user["token"])
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role").eq("id", current_user["user_id"]).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only.")
    return org_id, user_client


class LeadImportRow(BaseModel):
    company_name: str = Field(min_length=1, max_length=300)
    contact_email: EmailStr
    contact_name: str | None = Field(default=None, max_length=200)
    contact_title: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=300)
    context_note: str | None = Field(default=None, max_length=2000)


class LeadImportRequest(BaseModel):
    leads: list[LeadImportRow] = Field(min_length=1, max_length=_MAX_IMPORT_ROWS)
    # Off by default: importing a list and starting to mail it are separate
    # decisions, and an admin who pasted the wrong CSV should get a chance to
    # look at it before anything is drafted in their name.
    start_outreach: bool = False


class SendRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    subject: str | None = Field(default=None, max_length=300)


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class LogReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    # Whether to have the agent draft an answer. An admin who just wants the
    # reply on record (e.g. "not interested") shouldn't trigger an LLM run.
    draft_response: bool = True


class SalesSettingsUpdate(BaseModel):
    enabled: bool | None = None
    mode: Literal["shadow", "assisted", "autonomous"] | None = None
    sender_user_id: str | None = None
    tone: str | None = Field(default=None, max_length=1000)
    follow_up_delay_days: int | None = Field(default=None, ge=1, le=60)
    max_follow_ups: int | None = Field(default=None, ge=0, le=10)
    daily_send_cap: int | None = Field(default=None, ge=1, le=500)
    escalation_channel_id: str | None = None
    escalation_channel_name: str | None = None


# ── List / import ───────────────────────────────────────────────────────────


@router.get("")
async def list_leads(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10_000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    if status_filter and status_filter not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status_filter}'.")

    def _query() -> Any:
        q = (
            client.table("sales_leads")
            .select(
                "id, company_name, domain, contact_name, contact_email, "
                "contact_title, status, follow_up_count, next_follow_up_at, "
                "last_contacted_at, replied_at, created_at, updated_at",
                count="exact",
            )
            .eq("org_id", org_id)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        return q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

    res = await asyncio.to_thread(_query)
    return {"leads": res.data or [], "total": res.count or 0}


@router.post("/import")
async def import_leads(
    payload: LeadImportRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Bulk-insert leads from a CSV/JSON list.

    Upserts on (org_id, lower(contact_email)) so re-importing an overlapping
    list updates the existing rows instead of opening a second outreach thread
    to the same person — re-uploading a spreadsheet is a routine operator
    mistake and must not double-mail anyone.
    """
    org_id, client = await _require_admin(current_user)

    rows = [
        {
            "org_id": org_id,
            "company_name": r.company_name.strip(),
            "contact_email": str(r.contact_email).strip().lower(),
            "contact_name": (r.contact_name or "").strip() or None,
            "contact_title": (r.contact_title or "").strip() or None,
            "domain": (r.domain or "").strip() or None,
            "context_note": (r.context_note or "").strip() or None,
            "source": "csv_import",
        }
        for r in payload.leads
    ]
    # Dedupe within the request itself — Postgres rejects an upsert whose
    # command touches the same conflict key twice.
    seen: set[str] = set()
    deduped = []
    for row in rows:
        if row["contact_email"] in seen:
            continue
        seen.add(row["contact_email"])
        deduped.append(row)

    try:
        res = await asyncio.to_thread(
            lambda: client.table("sales_leads")
            .upsert(deduped, on_conflict="org_id,contact_email", ignore_duplicates=True)
            .execute()
        )
    except Exception as exc:
        log.warning("sales_import_failed org=%s err=%s", org_id, exc)
        raise HTTPException(status_code=400, detail=f"Import failed: {exc}") from exc

    inserted = res.data or []

    if payload.start_outreach and inserted:
        inngest_client = get_inngest_client()
        await inngest_client.send([
            inngest.Event(
                name="sales/lead-outreach",
                data={"org_id": org_id, "lead_id": r["id"], "kind": "first_touch"},
                id=f"sales-first-touch-{r['id']}",
            )
            for r in inserted
        ])

    return {
        "imported": len(inserted),
        "submitted": len(payload.leads),
        "outreach_started": bool(payload.start_outreach and inserted),
    }


# ── Settings ─────────────────────────────────────────────────────────────────
#
# NOTE: these MUST stay declared above the `/{lead_id}` routes below. FastAPI
# resolves in declaration order, so a literal path registered after a
# parameterized one at the same depth would be swallowed by it — GET
# /admin/sales/settings would bind lead_id="settings" and 404.


@router.get("/settings")
async def get_settings(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    return await so.load_settings(client, org_id)


@router.put("/settings")
async def update_settings(
    payload: SalesSettingsUpdate, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    # exclude_unset (not `is not None`) so an explicit null clears a field —
    # "no sending mailbox" and "don't notify a Slack channel" are real,
    # settable states, distinct from "this request didn't mention the field".
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    existing = await asyncio.to_thread(
        lambda: client.table("sales_settings")
        .select("org_id").eq("org_id", org_id).maybe_single().execute()
    )
    if existing and existing.data:
        result = await asyncio.to_thread(
            lambda: client.table("sales_settings").update(updates)
            .eq("org_id", org_id).execute()
        )
    else:
        result = await asyncio.to_thread(
            lambda: client.table("sales_settings")
            .insert({"org_id": org_id, **updates}).execute()
        )
    return (result.data or [{}])[0]


@router.get("/settings/senders")
async def list_connectable_senders(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Org members with a Gmail connection that has send permission.

    Outbound sales mail always comes from a named rep's own mailbox — there is
    no shared sending identity — so this list is what makes sending possible
    at all. An org with none of these is draft-only.
    """
    org_id, client = await _require_admin(current_user)
    rows = await asyncio.to_thread(
        lambda: client.table("gmail_integrations")
        .select("user_id, email_address, scopes").eq("org_id", org_id).execute()
    )
    senders = [
        {"user_id": r["user_id"], "email_address": r["email_address"]}
        for r in (rows.data or [])
        if gmail.has_send_scope(r.get("scopes"))
    ]
    return {"senders": senders}


# ── Lead detail ──────────────────────────────────────────────────────────────


async def _load_lead_or_404(client: Any, org_id: str, lead_id: str) -> dict[str, Any]:
    row = await asyncio.to_thread(
        lambda: client.table("sales_leads")
        .select("*").eq("id", lead_id).eq("org_id", org_id).maybe_single().execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    return row.data


@router.get("/{lead_id}")
async def get_lead(
    lead_id: str, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    lead = await _load_lead_or_404(client, org_id, lead_id)
    messages = await asyncio.to_thread(
        lambda: client.table("sales_messages")
        .select("*").eq("lead_id", lead_id).order("created_at").execute()
    )
    return {"lead": lead, "messages": messages.data or []}


# ── Actions ──────────────────────────────────────────────────────────────────


@router.post("/{lead_id}/send")
async def send_message(
    lead_id: str, payload: SendRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Approve and send a draft, with any edits the reviewer made.

    This is the normal path for outbound mail. 409s rather than sending if the
    org has no rep mailbox connected — we never fabricate a From address on a
    domain we don't control.
    """
    org_id, client = await _require_admin(current_user)
    lead = await _load_lead_or_404(client, org_id, lead_id)

    settings = await so.load_settings(client, org_id)
    creds = await so.resolve_sender(org_id=org_id, settings=settings)
    if not creds:
        raise HTTPException(
            status_code=409,
            detail="No sending mailbox connected. Pick a rep with Gmail connected "
                   "under Sales settings, then try again.",
        )

    latest_draft = await asyncio.to_thread(
        lambda: client.table("sales_messages")
        .select("id, subject, body")
        .eq("lead_id", lead_id).eq("author_type", "agent_draft").eq("status", "draft")
        .order("created_at", desc=True).limit(1).execute()
    )
    draft = (latest_draft.data or [{}])[0] if latest_draft and latest_draft.data else {}
    subject = payload.subject or draft.get("subject") or f"Quick question, {lead['company_name']}"

    try:
        sent = await gmail.send_email(
            access_token=creds["access_token"], sender=creds["email_address"],
            to=lead["contact_email"], subject=subject, body=payload.body,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=f"Gmail send failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    message_status = "sent" if draft.get("body") == payload.body else "edited_and_sent"

    if draft.get("id"):
        await asyncio.to_thread(
            lambda: client.table("sales_messages").update({
                "status": message_status, "sent_via": "gmail",
                "subject": subject, "body": payload.body,
                "provider_message_id": sent.get("message_id"),
            }).eq("id", draft["id"]).execute()
        )
    else:
        # Reviewer wrote from scratch with no pending draft — still a real
        # outbound message that belongs on the thread.
        await asyncio.to_thread(
            lambda: client.table("sales_messages").insert({
                "lead_id": lead_id, "org_id": org_id,
                "direction": "outbound", "author_type": "human",
                "subject": subject, "body": payload.body,
                "status": "sent", "sent_via": "gmail",
                "provider_message_id": sent.get("message_id"),
            }).execute()
        )

    await asyncio.to_thread(
        lambda: client.table("sales_leads").update({
            "follow_up_count": int(lead.get("follow_up_count") or 0) + 1,
        }).eq("id", lead_id).execute()
    )
    await so.mark_contacted(client, lead_id=lead_id, lead=lead, settings=settings)
    return {"ok": True, "status": message_status}


@router.post("/{lead_id}/reject")
async def reject_draft(
    lead_id: str, payload: RejectRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Discard the pending draft and park the lead for manual handling."""
    org_id, client = await _require_admin(current_user)
    await _load_lead_or_404(client, org_id, lead_id)

    await asyncio.to_thread(
        lambda: client.table("sales_messages").update({"status": "rejected"})
        .eq("lead_id", lead_id).eq("author_type", "agent_draft").eq("status", "draft")
        .execute()
    )
    await asyncio.to_thread(
        lambda: client.table("sales_leads").update({
            "status": "escalated",
            "escalation_reason": f"rejected_by_admin:{(payload.note or '').strip()[:200]}",
        }).eq("id", lead_id).execute()
    )
    return {"ok": True, "status": "escalated"}


@router.post("/{lead_id}/reply")
async def log_reply(
    lead_id: str, payload: LogReplyRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Record a reply the prospect sent, and optionally draft an answer.

    v1 has no connected sales inbox, so replies enter the system by an admin
    pasting them here. Logging one stops the follow-up cadence for that lead —
    nothing is more damaging than a "just checking in" nudge sent to someone
    who already answered.
    """
    org_id, client = await _require_admin(current_user)
    await _load_lead_or_404(client, org_id, lead_id)

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: client.table("sales_messages").insert({
            "lead_id": lead_id, "org_id": org_id,
            "direction": "inbound", "author_type": "prospect",
            "body": payload.body,
        }).execute()
    )
    await asyncio.to_thread(
        lambda: client.table("sales_leads").update({
            "status": "replied",
            "replied_at": now,
            "next_follow_up_at": None,
        }).eq("id", lead_id).execute()
    )

    if payload.draft_response:
        inngest_client = get_inngest_client()
        sig = hashlib.sha256(f"reply-{lead_id}-{now}".encode()).hexdigest()[:24]
        await inngest_client.send(
            inngest.Event(
                name="sales/lead-reply-logged",
                data={"org_id": org_id, "lead_id": lead_id},
                id=f"sales-reply-{sig}",
            )
        )
    return {"ok": True, "drafting": payload.draft_response}


@router.post("/{lead_id}/regenerate")
async def regenerate_draft(
    lead_id: str, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Re-run the drafting step for whatever stage this lead is at."""
    org_id, client = await _require_admin(current_user)
    lead = await _load_lead_or_404(client, org_id, lead_id)

    has_inbound = await asyncio.to_thread(
        lambda: client.table("sales_messages")
        .select("id").eq("lead_id", lead_id).eq("direction", "inbound")
        .limit(1).execute()
    )
    contacted = bool(lead.get("first_contacted_at"))
    if has_inbound and has_inbound.data:
        kind = "reply"
    elif contacted:
        kind = "follow_up"
    else:
        kind = "first_touch"

    # A pending draft is superseded by the one about to be generated —
    # otherwise the send endpoint could pick up whichever sorted first.
    await asyncio.to_thread(
        lambda: client.table("sales_messages").update({"status": "rejected"})
        .eq("lead_id", lead_id).eq("author_type", "agent_draft").eq("status", "draft")
        .execute()
    )

    inngest_client = get_inngest_client()
    sig = hashlib.sha256(
        f"regen-{lead_id}-{datetime.now(UTC).isoformat()}".encode()
    ).hexdigest()[:24]
    await inngest_client.send(
        inngest.Event(
            name="sales/lead-outreach",
            data={"org_id": org_id, "lead_id": lead_id, "kind": kind},
            id=f"sales-regenerate-{sig}",
        )
    )
    return {"ok": True, "status": "regenerating", "kind": kind}
