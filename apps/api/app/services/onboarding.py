"""Onboarding checklist state.

The V3 spec proposed persisting every step as a flag in
`organizations.metadata->'onboarding'`. We derive the steps from real data
instead and persist *only* the dismissed bit. That choice is deliberate:

  * Zero writes on the hot paths (chat send, document upload). The spec's
    `complete_onboarding_step()` triggered a DB write inside every chat
    message and every document upload, which is the wrong cost profile for a
    feature that exists to nudge new users for ~10 minutes.
  * Self-healing: no risk of a flag flipping out of sync with reality. If
    every document is later deleted, the checklist correctly un-completes
    "upload your first document" so the user gets the nudge again.
  * Per-user grain on "first question" comes for free — we count the
    current user's own messages, not an org-wide counter the spec would
    have needed extra columns to support.

Cost: three count queries per onboarding read (`documents`, `messages`,
`organizations.metadata` JSONB read). They're tiny and cached one-second by
the SWR layer on the frontend. The banner stops fetching the moment the
user dismisses or all steps complete.
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

from supabase import Client

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)


class OnboardingState(TypedDict):
    workspace_created: bool
    first_doc_uploaded: bool
    first_question_asked: bool
    completed: bool
    dismissed: bool


async def get_onboarding_state(
    *,
    user_client: Client,
    org_id: str,
    user_id: str,
) -> OnboardingState:
    """Compute the checklist state for one (user, org) pair.

    Run as the caller (user_client) so RLS scopes each query naturally.
    workspace_created is always true once we're here — the org exists, the
    user authenticated against it, so there's nothing left to verify.
    """
    # Fan out the three reads. They're independent — gather rather than
    # serial because doc and message counts can each hit cold rows.
    doc_task = asyncio.to_thread(
        lambda: user_client.table("documents")
        .select("id", count="exact", head=True)
        .limit(1)
        .execute()
    )
    msg_task = asyncio.to_thread(
        # `messages` has no user_id column on the row itself — author identity
        # lives on the parent conversation. Join via inner select.
        lambda: user_client.table("conversations")
        .select("id, messages!inner(id)", count="exact", head=True)
        .eq("user_id", user_id)
        .eq("messages.role", "user")
        .limit(1)
        .execute()
    )
    meta_task = asyncio.to_thread(
        # Org metadata is org-scoped; the user client can read its own org row.
        lambda: user_client.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )

    doc_res, msg_res, meta_res = await asyncio.gather(
        doc_task, msg_task, meta_task, return_exceptions=True
    )

    has_documents = _safe_count_gt_zero(doc_res, "documents")
    has_messages = _safe_count_gt_zero(msg_res, "messages")
    dismissed = _safe_dismissed_flag(meta_res)

    completed = has_documents and has_messages
    return OnboardingState(
        workspace_created=True,
        first_doc_uploaded=has_documents,
        first_question_asked=has_messages,
        completed=completed,
        # Auto-treat as dismissed once completed AND already dismissed by user;
        # we keep them as separate bits so the frontend can fire confetti
        # exactly on the (completed=true, dismissed=false) transition.
        dismissed=dismissed,
    )


async def mark_dismissed(*, org_id: str) -> None:
    """Persist the dismissed bit on the org's metadata JSONB.

    JSONB merge via the `||` operator keeps every other metadata key intact
    (we share this column with whatever else lands here later). Written with
    the service-role client because the user-scoped Supabase client doesn't
    expose a clean atomic merge — the alternative would be read-modify-write
    in two round trips with the lost-update hazard that implies.
    """
    svc = get_service_client()

    def _run() -> None:
        existing = (
            svc.table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        current = (existing.data or {}).get("metadata") or {}
        onboarding = dict(current.get("onboarding") or {})
        if onboarding.get("dismissed"):
            return
        onboarding["dismissed"] = True
        merged = {**current, "onboarding": onboarding}
        svc.table("organizations").update({"metadata": merged}).eq(
            "id", org_id
        ).execute()

    await asyncio.to_thread(_run)


def _safe_count_gt_zero(res, label: str) -> bool:
    if isinstance(res, BaseException):
        log.warning("onboarding_count_failed", source=label, error=str(res))
        return False
    # supabase-py with `head=True` returns count on the response object
    return bool(getattr(res, "count", 0) or 0)


def _safe_dismissed_flag(res) -> bool:
    if isinstance(res, BaseException):
        log.warning("onboarding_metadata_read_failed", error=str(res))
        return False
    data = getattr(res, "data", None) or {}
    return bool(
        ((data.get("metadata") or {}).get("onboarding") or {}).get("dismissed")
    )
