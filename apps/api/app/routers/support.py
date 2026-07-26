"""Admin surface for the customer support agent (fresh rebuild).

  GET  /admin/support                          — list tickets, filter by status
  GET  /admin/support/{ticket_id}               — thread + latest draft
  POST /admin/support/{ticket_id}/regenerate    — re-run the draft step
  POST /admin/support/{ticket_id}/send          — send (possibly edited) reply
  POST /admin/support/{ticket_id}/reject        — mark for manual handling
  GET  /admin/support/settings                  — org trust-mode config
  PUT  /admin/support/settings                  — update org trust-mode config
  GET  /admin/support/settings/senders          — connectable Gmail mailboxes

All reads/writes go through the user-scoped client (`get_user_client`), never
`get_service_client`, so Postgres RLS — not this router — is what actually
enforces org isolation and the admin-only gate. The `_require_admin` role
check below is belt-and-suspenders on top of that, matching the pattern used
by every other admin router in this codebase.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_user_client
from app.inngest import get_inngest_client
from app.observability import get_logger
from app.services.integrations import gmail

log = get_logger(__name__)

router = APIRouter(prefix="/admin/support", tags=["admin-support"])

_VALID_STATUSES = {
    "open", "pending_review", "awaiting_customer",
    "resolved", "escalated", "spam",
}


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


class SendReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    subject: str | None = None
    cc: str | None = None


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class SupportSettingsUpdate(BaseModel):
    enabled: bool | None = None
    mode: Literal["shadow", "assisted", "autonomous"] | None = None
    autonomous_categories: list[str] | None = None
    sender_user_id: str | None = None
    tone: str | None = None
    escalation_channel_id: str | None = None
    escalation_channel_name: str | None = None


# ── List / detail ───────────────────────────────────────────────────────────


@router.get("")
async def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    if status_filter and status_filter not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status_filter}'.")

    def _query() -> Any:
        q = (
            client.table("support_tickets")
            .select(
                "id, subject, from_email, from_name, status, category, "
                "priority, sentiment, created_at, updated_at, first_response_at",
                count="exact",
            )
            .eq("org_id", org_id)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        return q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()

    res = await asyncio.to_thread(_query)
    return {"tickets": res.data or [], "total": res.count or 0}


# ── Settings ─────────────────────────────────────────────────────────────────
#
# NOTE: these MUST stay declared above the `/{ticket_id}` routes below.
# FastAPI resolves in declaration order, so a literal path registered after a
# parameterized one at the same depth would be swallowed by it — GET
# /admin/support/settings would bind ticket_id="settings" and 404.


@router.get("/settings")
async def get_settings(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    row = await asyncio.to_thread(
        lambda: client.table("support_settings")
        .select("*").eq("org_id", org_id).maybe_single().execute()
    )
    if row and row.data:
        return row.data
    return {
        "org_id": org_id, "enabled": False, "mode": "assisted",
        "autonomous_categories": [], "sender_user_id": None, "tone": None,
        "escalation_channel_id": None, "escalation_channel_name": None,
    }


@router.put("/settings")
async def update_settings(
    payload: SupportSettingsUpdate, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    # exclude_unset (not `is not None`) so an explicit null clears a field —
    # "no sending mailbox" and "don't notify a Slack channel" are real,
    # settable states, distinct from "this request didn't mention the field".
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update.")

    existing = await asyncio.to_thread(
        lambda: client.table("support_settings")
        .select("org_id").eq("org_id", org_id).maybe_single().execute()
    )
    if existing and existing.data:
        result = await asyncio.to_thread(
            lambda: client.table("support_settings").update(updates)
            .eq("org_id", org_id).execute()
        )
    else:
        result = await asyncio.to_thread(
            lambda: client.table("support_settings").insert({"org_id": org_id, **updates}).execute()
        )
    return (result.data or [{}])[0]


@router.get("/settings/senders")
async def list_connectable_senders(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Org members with a Gmail connection that has send permission —
    candidates for support_settings.sender_user_id."""
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


# ── Ticket detail ────────────────────────────────────────────────────────────


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)

    ticket = await asyncio.to_thread(
        lambda: client.table("support_tickets")
        .select("*").eq("id", ticket_id).eq("org_id", org_id).maybe_single().execute()
    )
    if not ticket or not ticket.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    messages = await asyncio.to_thread(
        lambda: client.table("support_messages")
        .select("*").eq("ticket_id", ticket_id).order("created_at").execute()
    )
    return {"ticket": ticket.data, "messages": messages.data or []}


# ── Actions ──────────────────────────────────────────────────────────────────


@router.post("/{ticket_id}/regenerate")
async def regenerate_draft(
    ticket_id: str, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Re-runs the agent's triage+draft steps by re-firing the same event
    that created the ticket, using the original inbound message. Threading
    (dedupe_or_create_ticket) matches it back onto this same ticket rather
    than creating a duplicate."""
    org_id, client = await _require_admin(current_user)

    ticket = await asyncio.to_thread(
        lambda: client.table("support_tickets")
        .select("id, subject, from_email, from_name")
        .eq("id", ticket_id).eq("org_id", org_id).maybe_single().execute()
    )
    if not ticket or not ticket.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    inbound = await asyncio.to_thread(
        lambda: client.table("support_messages")
        .select("body")
        .eq("ticket_id", ticket_id).eq("direction", "inbound")
        .order("created_at", desc=True).limit(1).execute()
    )
    body = (inbound.data or [{}])[0].get("body") if inbound and inbound.data else None
    if not body:
        raise HTTPException(status_code=409, detail="No inbound message to redraft from.")

    t = ticket.data
    inngest_client = get_inngest_client()
    sig = hashlib.sha256(f"regen-{ticket_id}-{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:24]
    await inngest_client.send(
        inngest.Event(
            name="support/ticket-inbound",
            data={
                "org_id": org_id,
                "from_email": t["from_email"],
                "from_raw": t.get("from_name") or t["from_email"],
                "subject": t["subject"],
                "body": body,
            },
            id=f"support-regenerate-{sig}",
        )
    )
    return {"ok": True, "status": "regenerating"}


@router.post("/{ticket_id}/send")
async def send_reply(
    ticket_id: str, payload: SendReplyRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Sends a (possibly rep-edited) reply. v1 only sends through a connected,
    send-scoped Gmail mailbox (support_settings.sender_user_id) — there is no
    safe way to send *as* a customer's own domain when they're only using the
    forward-to-brain inbound address, so those orgs get a clear error here
    rather than a silently spoofed From header."""
    org_id, client = await _require_admin(current_user)

    ticket = await asyncio.to_thread(
        lambda: client.table("support_tickets")
        .select("id, subject, from_email, status, first_response_at")
        .eq("id", ticket_id).eq("org_id", org_id).maybe_single().execute()
    )
    if not ticket or not ticket.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    settings = await asyncio.to_thread(
        lambda: client.table("support_settings")
        .select("sender_user_id").eq("org_id", org_id).maybe_single().execute()
    )
    sender_user_id = (settings.data or {}).get("sender_user_id") if settings and settings.data else None
    if not sender_user_id:
        raise HTTPException(
            status_code=409,
            detail="No support mailbox connected. Connect a Gmail account with "
                   "send permission under Support settings, then try again.",
        )

    creds = await gmail.get_user_credentials(org_id=org_id, user_id=sender_user_id)
    if not creds or not gmail.has_send_scope(creds.get("scopes")):
        raise HTTPException(
            status_code=409,
            detail="Connected support mailbox no longer has send permission. "
                   "Reconnect Gmail under Support settings.",
        )

    t = ticket.data
    subject = payload.subject or (
        f"Re: {t['subject']}" if not t["subject"].lower().startswith("re:") else t["subject"]
    )
    try:
        sent = await gmail.send_email(
            access_token=creds["access_token"], sender=creds["email_address"],
            to=t["from_email"], subject=subject, body=payload.body, cc=payload.cc,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=f"Gmail send failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    latest_draft = await asyncio.to_thread(
        lambda: client.table("support_messages")
        .select("body")
        .eq("ticket_id", ticket_id).eq("author_type", "agent_draft")
        .order("created_at", desc=True).limit(1).execute()
    )
    draft_body = (latest_draft.data or [{}])[0].get("body") if latest_draft and latest_draft.data else None
    message_status = "sent" if draft_body == payload.body else "edited_and_sent"

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: client.table("support_messages").insert({
            "ticket_id": ticket_id, "org_id": org_id,
            "direction": "outbound", "author_type": "human",
            "body": payload.body, "status": message_status,
            "sent_via": "gmail", "provider_message_id": sent.get("message_id"),
        }).execute()
    )
    await asyncio.to_thread(
        lambda: client.table("support_tickets").update({
            "status": "awaiting_customer",
            "first_response_at": t.get("first_response_at") or now,
        }).eq("id", ticket_id).execute()
    )
    return {"ok": True, "status": "sent"}


@router.post("/{ticket_id}/reject")
async def reject_draft(
    ticket_id: str, payload: RejectRequest, current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, client = await _require_admin(current_user)
    ticket = await asyncio.to_thread(
        lambda: client.table("support_tickets")
        .select("id").eq("id", ticket_id).eq("org_id", org_id).maybe_single().execute()
    )
    if not ticket or not ticket.data:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    await asyncio.to_thread(
        lambda: client.table("support_tickets").update({
            "status": "escalated",
            "escalation_reason": f"rejected_by_admin:{(payload.note or '').strip()[:200]}",
            "escalated_at": datetime.now(UTC).isoformat(),
        }).eq("id", ticket_id).execute()
    )
    return {"ok": True, "status": "escalated"}


