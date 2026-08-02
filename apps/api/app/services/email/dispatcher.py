"""Fire-and-queue helper for transactional emails.

Route handlers call `send_email_event(...)` which emits an Inngest event.
The actual rendering + Resend call lives in app.services.email.worker so
the caller doesn't block on a possibly-flaky third party.
"""
from __future__ import annotations

from typing import Any, Literal

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger

log = get_logger(__name__)

EventType = Literal[
    "invite",
    "welcome",
    "document_ready",
    "quota_warning",
    "quota_exceeded",
    "weekly_digest",
    "approval_request",
    "approval_resolved",
    "approval_reminder",
    "onboarding_welcome",
    "acknowledgement_reminder",
    "scheduled_usage_summary",
    "scheduled_knowledge_health",
    "internal_announcement",
    "weekly_briefing",
    "recruiting_published",
    # ── Onboarding v2 ─────────────────────────────────────────────────────
    "onboarding_loi_ready",            # HR: LOIgenerated, please sign
    "onboarding_loi_to_candidate",     # Candidate: signed LOIfrom HR
    "onboarding_bgv_request",          # Reference: please verify candidate
    "onboarding_bgv_reminder",         # Reference: gentle nudge
    "onboarding_candidate_refs_reminder",  # Candidate: please submit refs
    "onboarding_offer_bundle_ready",   # HR: AL + NDA ready for review
    "onboarding_step_review_ready",    # HR: an org-composed step's docs need approving
    "onboarding_offer_to_candidate",   # Candidate: appointment letter + NDA
    "onboarding_policies_pending",     # Candidate: please acknowledge policies
    "onboarding_induction_ready",      # Candidate: your induction document
    "onboarding_esign_stalled",        # HR: signing envelope >48h without completion
    "onboarding_sign_your_turn",       # Signer: it's your turn to sign (apps/esign routed flow)
    "onboarding_documents_requested",  # Candidate: upload this step's checklist
    "onboarding_references_requested", # Candidate: name your referees
    "onboarding_documents_sent",       # Candidate: documents from an org-composed step
    "onboarding_step_approval_needed", # HR: the candidate acted, check it before the run moves on
    "onboarding_step_rejected",        # Candidate: HR sent it back, here is what to redo
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
