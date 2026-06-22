"""Admin support queue (Agent Day 12 Part A).

Endpoints:
    GET    /admin/support                — list tickets (paginated, filtered)
    GET    /admin/support/{ticket_id}    — full detail
    POST   /admin/support/{ticket_id}/regenerate — rerun the drafting agent
    POST   /admin/support/{ticket_id}/send       — send the (possibly edited)
                                                   draft via Gmail
    POST   /admin/support/{ticket_id}/reject     — mark as rejected
    POST   /admin/support/settings/support-channel — admin sets the Slack
                                                     support channel for
                                                     ticket notifications

Auth: admin-only across the board. The ticket bodies contain customer PII so
we mirror agent_runs' RLS posture (admin select/update).

Send flow:
    The autonomous agent saved the draft on `support_tickets.ai_draft_body`.
    When the admin clicks "Send via Gmail" we:
        1. Mint a `conversations` row owned by the admin (titled "Support: …")
        2. Insert two messages: user role (inbound) + assistant role (draft)
        3. POST the assistant message_id at the existing Gmail send pipeline
           — that path already handles delivery_status, retries, etc.
    This keeps the Gmail audit + retry plumbing reused unchanged while still
    letting the autonomous agent run with no user context.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import inngest as inngest_pkg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.integrations import slack as slack_service

log = get_logger(__name__)

router = APIRouter(prefix="/admin/support", tags=["support"])


# ── Helpers ────────────────────────────────────────────────────────────────


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _require_admin(*, user_id: str, token: str) -> None:
    """Two layers of defense: explicit check + RLS. RLS would reject the
    write anyway, but we want a clean 403 message rather than a confusing
    DB-layer 500."""
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only workspace admins can access support tickets.")


def _ticket_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Shape returned by the list endpoint — strips heavy fields (body,
    full sources) and surfaces what the queue UI needs."""
    return {
        "id": row["id"],
        "from_email": row.get("from_email"),
        "from_name": row.get("from_name"),
        "subject": row.get("subject") or "",
        "category": row.get("category"),
        "status": row.get("status"),
        "classifier_confidence": row.get("classifier_confidence"),
        "ai_draft_confidence": row.get("ai_draft_confidence"),
        "has_draft": bool(row.get("ai_draft_body")),
        "agent_run_id": row.get("agent_run_id"),
        "created_at": row.get("created_at"),
        "resolved_at": row.get("resolved_at"),
    }


# ── Models ─────────────────────────────────────────────────────────────────


class TicketListResponse(BaseModel):
    tickets: list[dict[str, Any]]
    total: int


class TicketDetailResponse(BaseModel):
    ticket: dict[str, Any]


class TicketSendRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=200_000)
    subject: str | None = Field(default=None, max_length=998)
    cc: str | None = Field(default=None, max_length=320)


class TicketRejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class TicketActionResponse(BaseModel):
    ok: bool
    status: str
    detail: str | None = None


class SupportChannelRequest(BaseModel):
    channel_id: str | None = Field(default=None, max_length=40)
    channel_name: str | None = Field(default=None, max_length=200)

    @field_validator("channel_id")
    @classmethod
    def _channel_id_shape(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        # Slack channel ids are uppercase alphanumeric starting with C/G/D.
        if not v.startswith(("C", "G", "D")):
            raise ValueError("channel_id must be a Slack channel id (Cxxx).")
        return v


class SupportChannelResponse(BaseModel):
    channel_id: str | None
    channel_name: str | None


# ── List + detail ───────────────────────────────────────────────────────────


@router.get("", response_model=TicketListResponse)
async def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(verify_jwt),
) -> TicketListResponse:
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        q = (
            svc.table("support_tickets")
            .select(
                "id, from_email, from_name, subject, category, status, "
                "classifier_confidence, ai_draft_confidence, ai_draft_body, "
                "agent_run_id, created_at, resolved_at",
                count="exact",
            )
            .eq("org_id", org_id)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
        return q.execute()

    result = await asyncio.to_thread(_run)
    rows = result.data or []
    total = getattr(result, "count", None) or len(rows)
    return TicketListResponse(
        tickets=[_ticket_to_summary(r) for r in rows],
        total=total,
    )


@router.get("/settings/support-channel", response_model=SupportChannelResponse)
async def get_support_channel(
    current_user: dict = Depends(verify_jwt),
) -> SupportChannelResponse:
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    meta = (row.data or {}).get("metadata") or {} if row and row.data else {}
    return SupportChannelResponse(
        channel_id=meta.get("support_channel_id"),
        channel_name=meta.get("support_channel_name"),
    )


@router.put("/settings/support-channel", response_model=SupportChannelResponse)
async def set_support_channel(
    body: SupportChannelRequest,
    current_user: dict = Depends(verify_jwt),
) -> SupportChannelResponse:
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()

    def _run() -> dict[str, Any]:
        existing = (
            svc.table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        meta = (existing.data or {}).get("metadata") if existing and existing.data else {}
        meta = meta or {}
        if body.channel_id:
            meta["support_channel_id"] = body.channel_id
            meta["support_channel_name"] = body.channel_name
        else:
            meta.pop("support_channel_id", None)
            meta.pop("support_channel_name", None)
        svc.table("organizations").update({"metadata": meta}).eq("id", org_id).execute()
        return meta

    new_meta = await asyncio.to_thread(_run)
    return SupportChannelResponse(
        channel_id=new_meta.get("support_channel_id"),
        channel_name=new_meta.get("support_channel_name"),
    )


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: str,
    current_user: dict = Depends(verify_jwt),
) -> TicketDetailResponse:
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .select("*")
        .eq("id", ticket_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="ticket_not_found")

    # Pull the linked message + delivery status if a send already happened.
    msg_id = row.data.get("ai_draft_message_id")
    delivery: dict[str, Any] | None = None
    if msg_id:
        msg = await asyncio.to_thread(
            lambda: svc.table("messages")
            .select("id, delivery_status, conversation_id")
            .eq("id", msg_id)
            .maybe_single()
            .execute()
        )
        if msg and msg.data:
            delivery = msg.data.get("delivery_status")
    detail = dict(row.data)
    detail["delivery_status"] = delivery
    return TicketDetailResponse(ticket=detail)


# ── Actions ────────────────────────────────────────────────────────────────


@router.post("/{ticket_id}/regenerate", response_model=TicketActionResponse)
async def regenerate_draft(
    ticket_id: str,
    current_user: dict = Depends(verify_jwt),
) -> TicketActionResponse:
    """Re-run the drafting agent on an existing ticket. Useful when the
    knowledge base has been updated since the original draft."""
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .select("*")
        .eq("id", ticket_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    ticket = row.data
    if ticket.get("status") == "sent":
        raise HTTPException(status_code=409, detail="ticket_already_sent")

    # Reset to pending so the next agent run can drop a fresh draft.
    await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .update({"status": "pending", "ai_draft_body": None, "ai_draft_sources": None, "ai_draft_confidence": None})
        .eq("id", ticket_id)
        .execute()
    )

    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="support/email-received",
            data={
                "org_id": org_id,
                "from_email": ticket["from_email"],
                "from_name": ticket.get("from_name"),
                "subject": ticket.get("subject") or "",
                "body": ticket.get("body") or "",
                "category": ticket.get("category") or "support",
                "classifier_confidence": ticket.get("classifier_confidence"),
                "classifier_reason": ticket.get("classifier_reason"),
            },
            # New idempotency suffix so this run doesn't dedupe to the prior agent.
            id=f"support-regen-{ticket_id}-{uuid.uuid4().hex[:12]}",
        )
    )
    return TicketActionResponse(ok=True, status="regenerating", detail="Drafting agent re-queued.")


@router.post("/{ticket_id}/send", response_model=TicketActionResponse)
async def send_ticket(
    ticket_id: str,
    body: TicketSendRequest,
    current_user: dict = Depends(verify_jwt),
) -> TicketActionResponse:
    """Send the (possibly edited) draft via Gmail.

    We mint a message owned by the admin so the existing Gmail send
    pipeline's delivery_status / approval audit machinery reuses unchanged.
    """
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .select("*")
        .eq("id", ticket_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    ticket = row.data
    if ticket.get("status") == "sent":
        raise HTTPException(status_code=409, detail="ticket_already_sent")
    if not ticket.get("from_email"):
        raise HTTPException(status_code=400, detail="ticket_missing_recipient")

    # Verify the admin has Gmail send scope before we mint a message.
    gmail_row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("id, scopes")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not gmail_row or not gmail_row.data:
        raise HTTPException(status_code=400, detail="gmail_not_connected")
    scopes = gmail_row.data.get("scopes") or []
    from app.services.integrations import gmail as gmail_service
    if not gmail_service.has_send_scope(scopes):
        raise HTTPException(status_code=403, detail="gmail_send_scope_missing")

    subject = body.subject or f"Re: {ticket.get('subject') or ''}".strip()
    if len(subject) > 998:
        subject = subject[:998]

    # 1. Conversation row (admin-owned, hidden by default in chat list via title prefix).
    conversation_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: svc.table("conversations").insert({
            "id": conversation_id,
            "org_id": org_id,
            "user_id": user_id,
            "title": f"Support: {(ticket.get('subject') or '(no subject)')[:80]}",
        }).execute()
    )

    # 2. User-role message — the inbound email body for posterity.
    inbound_msg_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: svc.table("messages").insert({
            "id": inbound_msg_id,
            "conversation_id": conversation_id,
            "org_id": org_id,
            "role": "user",
            "content": (
                f"Inbound support email\n"
                f"From: {ticket.get('from_email')}\n"
                f"Subject: {ticket.get('subject') or ''}\n\n"
                f"{ticket.get('body') or ''}"
            ),
            "sources": None,
        }).execute()
    )

    # 3. Assistant-role message — the draft (possibly edited by the admin
    #    in the UI before clicking send). We carry the sources from the
    #    agent run so the audit shows what the AI cited.
    draft_msg_id = str(uuid.uuid4())
    confidence = ticket.get("ai_draft_confidence")
    metadata: dict[str, Any] = {}
    if confidence is not None:
        metadata["confidence"] = {"score": confidence}
    await asyncio.to_thread(
        lambda: svc.table("messages").insert({
            "id": draft_msg_id,
            "conversation_id": conversation_id,
            "org_id": org_id,
            "role": "assistant",
            "content": body.body,
            "sources": ticket.get("ai_draft_sources"),
            "metadata": metadata or None,
        }).execute()
    )

    # 4. Link draft back to ticket, optimistic state for the queue UI.
    await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .update({
            "ai_draft_message_id": draft_msg_id,
            "status": "sent",
            "resolved_by": user_id,
            "resolved_at": datetime.now(UTC).isoformat(),
        })
        .eq("id", ticket_id)
        .execute()
    )

    # 5. Fan to Gmail send pipeline. We replicate the gmail_router's send
    #    semantics here — stamp delivery_status optimistically, then queue
    #    the Inngest event. This is preferred over an HTTP self-call.
    job_id = str(uuid.uuid4())
    queued_at = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gmail",
                "status": "queued",
                "recipient": ticket["from_email"],
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", draft_msg_id)
        .execute()
    )
    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="gmail/send-email",
            data={
                "job_id": job_id,
                "message_id": draft_msg_id,
                "org_id": org_id,
                "user_id": user_id,
                "to": ticket["from_email"],
                "subject": subject,
                "body": body.body,
                "cc": body.cc,
                "reply_to": None,
            },
            id=f"gmail-send-{job_id}",
        )
    )
    return TicketActionResponse(
        ok=True, status="sent", detail=f"Reply queued to {ticket['from_email']}.",
    )


@router.post("/{ticket_id}/reject", response_model=TicketActionResponse)
async def reject_ticket(
    ticket_id: str,
    body: TicketRejectRequest,
    current_user: dict = Depends(verify_jwt),
) -> TicketActionResponse:
    """Mark the ticket rejected without sending. Used when the admin wants
    to ignore the email entirely or handle out-of-band."""
    user_id, org_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .select("id, status")
        .eq("id", ticket_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    if row.data.get("status") == "sent":
        raise HTTPException(status_code=409, detail="ticket_already_sent")

    await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .update({
            "status": "rejected",
            "resolved_by": user_id,
            "resolved_at": datetime.now(UTC).isoformat(),
        })
        .eq("id", ticket_id)
        .execute()
    )
    _ = body  # note is currently informational only — not stored
    return TicketActionResponse(ok=True, status="rejected")


# Used by SupportResponseAgent.notify_support_team — exposed here so tests
# can spot-check resolution without importing the agent.
async def _resolve_support_channel(org_id: str) -> tuple[str | None, str | None]:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("metadata").eq("id", org_id).maybe_single().execute()
    )
    meta = (row.data or {}).get("metadata") or {} if row and row.data else {}
    return meta.get("support_channel_id"), meta.get("support_channel_name")


_ = slack_service  # silence unused-import; kept for future delete-message use
