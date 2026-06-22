"""Fire-and-queue helper for transactional emails.

Route handlers call `send_email_event(...)` which emits an Inngest event.
The actual rendering + Resend call lives in app.services.email.worker so
the caller doesn't block on a possibly-flaky third party.
"""
from __future__ import annotations

from typing import Any, Literal

from app.inngest.client import get_inngest_client
from app.observability import get_logger

import inngest

log = get_logger(__name__)

EventType = Literal[
    "invite",
    "welcome",
    "document_ready",
    "quota_warning",
    "quota_exceeded",
    "weekly_digest",
    "knowledge_refresh",
    "knowledge_gap_alert",
    "approval_request",
    "approval_resolved",
    "approval_reminder",
    "onboarding_welcome",
    "acknowledgement_reminder",
    "scheduled_usage_summary",
    "scheduled_knowledge_health",
]


async def send_email_event(
    *,
    event_type: EventType,
    to: str,
    user_id: str | None,
    data: dict[str, Any],
    org_id: str | None = None,
    dedupe_key: str | None = None,
) -> None:
    """Queue a transactional email for delivery via Inngest.

    The worker enforces idempotency against `email_events`:
        - `dedupe_key=None` → at-most-once per (user_id, event_type)
          for the user's lifetime (e.g. welcome).
        - `dedupe_key=<x>`  → at-most-once per (user_id, event_type, x)
          (e.g. document_id for document_ready, YYYY-MM for quota_*).

    For pre-acceptance invites we have no user_id; dedupe falls through to
    (recipient, event_type, dedupe_key) via the partial index.
    """
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="email/send",
            data={
                "event_type": event_type,
                "to": to,
                "user_id": user_id,
                "org_id": org_id,
                "dedupe_key": dedupe_key,
                "template_data": data,
            },
        )
    )
    log.info(
        "email_enqueued",
        event_type=event_type,
        recipient=to,
        user_id=user_id,
        dedupe_key=dedupe_key,
    )
