"""Shared support-reply delivery (extracted from routers/support.py send_ticket).

Both paths that send a support reply funnel through `deliver_ticket_reply`:

  * the admin "Send via Gmail" endpoint  (a human clicks send)
  * the autonomous auto-send path        (agent sends a high-confidence draft)

Keeping the message-minting + Gmail fan-out in one place means the two paths
can't drift: they mint the same conversation/message rows, stamp the same
optimistic delivery_status, and reuse the Day-6 Gmail send pipeline unchanged.

The only difference is the *sender identity* (whose Gmail sends) and the ticket
`status` stamped afterwards ('sent' for a human, 'auto_sent' for the agent).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.integrations import gmail as gmail_service

log = get_logger(__name__)

# Distinctive tail-phrase of the escalation fallback the draft prompt tells the
# model to emit when the KB doesn't cover the question. Centralised here (rather
# than in the agent) so the agent's confidence floor and the metrics endpoint's
# escalation counting both key off one string that stays in sync with the prompt.
ESCALATION_MARKER = "escalating to a teammate"


def is_escalation_body(body: str | None) -> bool:
    """True when a stored draft body is the KB-miss escalation fallback."""
    return bool(body) and ESCALATION_MARKER in body.lower()


# Default confidence bar for auto-send when an org enables it without setting
# its own. Deliberately high: only drafts the heuristic is very sure about go
# out unattended. Escalation drafts are floored to 1.0 upstream so they can
# never clear this bar. Shared by the settings endpoint and the agent so the
# "enabled but no explicit threshold" default can't drift between them.
DEFAULT_AUTOSEND_THRESHOLD = 8.0


async def resolve_autosend_sender(org_id: str) -> str | None:
    """Pick the Gmail identity an autonomous auto-send should send *as*.

    The agent has no user session, so a high-confidence auto-reply still needs
    a real connected mailbox to go out from. Preference order:

      1. organizations.metadata.support_sender_user_id (explicitly designated)
      2. any admin in the org whose gmail_integrations row has the send scope

    Returns the user_id, or None when no eligible mailbox exists (caller then
    leaves the draft in the review queue rather than silently dropping it).
    """
    svc = get_service_client()

    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("metadata").eq("id", org_id).maybe_single().execute()
    )
    meta = (org.data or {}).get("metadata") or {} if org and org.data else {}
    preferred = meta.get("support_sender_user_id")

    rows = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("user_id, scopes").eq("org_id", org_id).execute()
    )
    eligible: list[str] = [
        r["user_id"]
        for r in (rows.data or [])
        if r.get("user_id") and gmail_service.has_send_scope(r.get("scopes"))
    ]
    if not eligible:
        return None
    if preferred and preferred in eligible:
        return preferred

    # Fall back to an admin among the eligible mailboxes so we don't send as a
    # random member. If none of the eligible senders are admins, use the first
    # eligible mailbox (any connected sender is better than dropping the reply).
    admins = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id").eq("org_id", org_id).eq("role", "admin")
        .in_("id", eligible).execute()
    )
    admin_ids = [u["id"] for u in (admins.data or [])]
    return admin_ids[0] if admin_ids else eligible[0]


async def deliver_ticket_reply(
    *,
    org_id: str,
    sender_user_id: str,
    ticket: dict[str, Any],
    body: str,
    subject: str | None = None,
    cc: str | None = None,
    status: str = "sent",
) -> dict[str, Any]:
    """Mint the conversation + messages for a support reply and fan the Gmail
    send job. Returns {message_id, job_id, to}.

    Caller must have already verified `sender_user_id` has Gmail send scope
    (the admin endpoint checks the caller; the auto-send path resolves an
    eligible sender via `resolve_autosend_sender`).
    """
    svc = get_service_client()
    ticket_id = ticket["id"]
    to_email = ticket["from_email"]

    reply_subject = subject or f"Re: {ticket.get('subject') or ''}".strip()
    if len(reply_subject) > 998:
        reply_subject = reply_subject[:998]

    # 1. Conversation row (owned by the sending identity, prefixed so the chat
    #    list can hide support threads).
    conversation_id = str(uuid.uuid4())
    await asyncio.to_thread(
        lambda: svc.table("conversations").insert({
            "id": conversation_id,
            "org_id": org_id,
            "user_id": sender_user_id,
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

    # 3. Assistant-role message — the draft. Carry the agent's cited sources +
    #    confidence so the audit shows what the AI relied on.
    draft_msg_id = str(uuid.uuid4())
    confidence = ticket.get("ai_draft_confidence")
    metadata: dict[str, Any] = {}
    if confidence is not None:
        metadata["confidence"] = {"score": confidence}
    if status == "auto_sent":
        metadata["auto_sent"] = True
    await asyncio.to_thread(
        lambda: svc.table("messages").insert({
            "id": draft_msg_id,
            "conversation_id": conversation_id,
            "org_id": org_id,
            "role": "assistant",
            "content": body,
            "sources": ticket.get("ai_draft_sources"),
            "metadata": metadata or None,
        }).execute()
    )

    # 4. Link draft back to ticket, optimistic state for the queue UI.
    await asyncio.to_thread(
        lambda: svc.table("support_tickets")
        .update({
            "ai_draft_message_id": draft_msg_id,
            "status": status,
            "resolved_by": sender_user_id,
            "resolved_at": datetime.now(UTC).isoformat(),
        })
        .eq("id", ticket_id)
        .execute()
    )

    # 5. Fan to Gmail send pipeline (stamp optimistic delivery_status, then
    #    queue the Inngest event — same semantics as the gmail_router).
    job_id = str(uuid.uuid4())
    queued_at = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gmail",
                "status": "queued",
                "recipient": to_email,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", draft_msg_id)
        .execute()
    )
    # Lazy import — app.inngest.__init__ eagerly loads the support agent, which
    # imports this module; importing the client at module top would cycle.
    import inngest as inngest_pkg

    from app.inngest.client import get_inngest_client
    client = get_inngest_client()
    await client.send(
        inngest_pkg.Event(
            name="gmail/send-email",
            data={
                "job_id": job_id,
                "message_id": draft_msg_id,
                "org_id": org_id,
                "user_id": sender_user_id,
                "to": to_email,
                "subject": reply_subject,
                "body": body,
                "cc": cc,
                "reply_to": None,
            },
            id=f"gmail-send-{job_id}",
        )
    )
    return {"message_id": draft_msg_id, "job_id": job_id, "to": to_email}
