"""Inngest worker for the Gmail send pipeline (V5 Day 3).

Triggered by `gmail/send-email` events fanned out from the gmail router.
Steps:
    1. Resolve the user's credentials (refresh access_token if expired).
    2. Send via Gmail REST.
    3. Flip messages.delivery_status to "sent" + carry the gmail_message_id.
    4. Bump gmail_integrations.last_used_at.

On permanent auth failures (401 / 403 → PermissionError), we DON'T raise:
retrying a revoked token will never succeed, and the user-facing fix is "go
reconnect Gmail." We flag delivery_status as failed with a stable error code
the frontend uses to render the re-auth banner inline on that message.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.integrations import gmail

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


async def _mark_delivery(
    *,
    message_id: str,
    org_id: str,
    delivery: dict[str, Any],
) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({"delivery_status": delivery})
        .eq("id", message_id)
        .eq("org_id", org_id)
        .execute()
    )


async def _bump_last_used(*, org_id: str, user_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .update({"last_used_at": datetime.now(timezone.utc).isoformat()})
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .execute()
    )


@_inngest_client.create_function(
    fn_id="gmail-send-email",
    trigger=inngest.TriggerEvent(event="gmail/send-email"),
    retries=3,
    # Per-user concurrency cap so one runaway user can't starve everyone
    # else's Gmail sends. Gmail's per-user send quota is generous; 2 in
    # flight is plenty for a human-driven workflow.
    concurrency=[inngest.Concurrency(limit=2, key="event.data.user_id", scope="fn")],
)
async def gmail_send_email(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    job_id: str = data["job_id"]
    message_id: str = data["message_id"]
    org_id: str = data["org_id"]
    user_id: str = data["user_id"]

    # Step 1: credentials (refreshes if needed).
    creds = await step.run(
        "resolve-credentials",
        lambda: gmail.get_user_credentials(org_id=org_id, user_id=user_id),
    )
    if not creds:
        # User disconnected between queueing and execution. Surface the same
        # failure code as a missing scope so the UI converges on one banner.
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gmail",
                "status": "failed",
                "recipient": data["to"],
                "job_id": job_id,
                "error": "gmail_not_connected",
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "failed", "reason": "gmail_not_connected"}

    if not gmail.has_send_scope(creds.get("scopes")):
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gmail",
                "status": "failed",
                "recipient": data["to"],
                "job_id": job_id,
                "error": "gmail_send_scope_missing",
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "failed", "reason": "gmail_send_scope_missing"}

    # Decode any attachments the router built (e.g. the Sources_used.txt
    # rendered from the citation context). They ride the event as base64
    # so the Inngest JSON payload stays well-formed.
    raw_attachments = data.get("attachments") or []
    decoded_attachments: list[dict[str, Any]] = []
    for att in raw_attachments:
        b64 = att.get("content_b64")
        if not b64:
            continue
        try:
            decoded = base64.b64decode(b64)
        except Exception as exc:
            log.warning("attachment decode failed (%s): %s", att.get("filename"), exc)
            continue
        decoded_attachments.append({
            "filename": att.get("filename") or "attachment.bin",
            "mime_type": att.get("mime_type") or "application/octet-stream",
            "content": decoded,
        })

    # Step 2: send (with permission-error short-circuit for unrecoverable
    # auth failures so we don't waste retry budget on revoked tokens).
    try:
        result = await step.run(
            "send-via-gmail",
            lambda: gmail.send_email(
                access_token=creds["access_token"],
                sender=creds["email_address"],
                to=data["to"],
                subject=data["subject"],
                body=data["body"],
                cc=data.get("cc"),
                reply_to=data.get("reply_to"),
                attachments=decoded_attachments or None,
            ),
        )
    except PermissionError as exc:
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gmail",
                "status": "failed",
                "recipient": data["to"],
                "job_id": job_id,
                "error": str(exc) or "gmail_send_unauthorized",
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "failed", "reason": str(exc)}

    # Step 3: record success on the message.
    await step.run(
        "record-delivery",
        lambda: _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "gmail",
                "status": "sent",
                "recipient": data["to"],
                "job_id": job_id,
                "external_id": result.get("message_id"),
                "thread_id": result.get("thread_id"),
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            },
        ),
    )

    # Step 4: bump the per-user last_used_at — surfaces in the integrations
    # card so admins can tell if a user is actively using Gmail send.
    await step.run(
        "bump-last-used",
        lambda: _bump_last_used(org_id=org_id, user_id=user_id),
    )

    return {"status": "sent", "gmail_message_id": result.get("message_id")}


FUNCTIONS = [gmail_send_email]
