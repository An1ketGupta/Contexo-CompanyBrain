"""Org-scoped endpoints introduced for V3 Day 1.

Two surfaces live here, intentionally co-located because they share a single
job — power the empty-state banner and onboarding checklist on the chat
surface:

  * GET /organizations/document-status — counts of total / ready / processing
    docs. Backs the "no documents" / "still processing" banner shown above
    the chat input. Tiny, cheap, frequently polled.
  * GET /organizations/onboarding      — derived checklist state.
  * POST /organizations/onboarding/dismiss — persists the dismissed bit.

We keep these on /organizations/* (not /documents/*) because the banner is
about the org's onboarding posture, not about any single document.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_user_client
from app.observability import get_logger
from app.services.onboarding import (
    OnboardingState,
    get_onboarding_state,
    mark_dismissed,
)

log = get_logger(__name__)

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/document-status")
async def document_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return doc counts the chat banner uses to decide its empty / processing
    state. Three states the UI cares about:

      * total = 0                                → "knowledge base empty"
      * total > 0 AND ready = 0 AND processing>0 → "documents still processing"
      * ready > 0                                → banner hidden

    We fold pending + processing into a single "processing" count because
    from the user's perspective both mean "not yet usable". Internally those
    are distinct (pending = pre-Inngest, processing = inside the pipeline)
    but exposing the distinction here would be a footgun for the banner copy.
    """
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        return {"total": 0, "ready": 0, "processing": 0, "failed": 0, "has_ready": False}

    client = get_user_client(current_user["token"])

    # Four head-counts, one round-trip each. We *could* fetch one row of
    # status strings and tally in Python, but the GIN/B-tree on status is
    # already there from migration 001 and supabase-py's `head=True` ships
    # zero rows back. Four pings of <2ms each on a warm pool is fine.
    async def _count(status_value: str | None) -> int:
        def _run() -> int:
            q = client.table("documents").select("id", count="exact", head=True)
            if status_value is not None:
                q = q.eq("status", status_value)
            res = q.execute()
            return int(getattr(res, "count", 0) or 0)
        return await asyncio.to_thread(_run)

    total, ready, processing, pending, failed = await asyncio.gather(
        _count(None),
        _count("ready"),
        _count("processing"),
        _count("pending"),
        _count("failed"),
        return_exceptions=False,
    )

    return {
        "total": total,
        "ready": ready,
        "processing": processing + pending,
        "failed": failed,
        "has_ready": ready > 0,
    }


@router.get("/onboarding")
async def onboarding_state(
    current_user: dict = Depends(verify_jwt),
) -> OnboardingState:
    org_id: str | None = current_user.get("org_id")
    user_id: str = current_user["user_id"]
    if not org_id:
        # No org → no checklist to render. Treat as completed+dismissed so
        # the banner stays hidden in this edge case (the caller is mid-signup
        # or the org was deleted).
        return OnboardingState(
            workspace_created=False,
            first_doc_uploaded=False,
            first_question_asked=False,
            completed=False,
            dismissed=True,
        )

    client = get_user_client(current_user["token"])
    return await get_onboarding_state(
        user_client=client, org_id=org_id, user_id=user_id
    )


@router.post("/onboarding/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def onboarding_dismiss(
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Persist `metadata.onboarding.dismissed = true` for the caller's org.

    Org-wide: once one teammate dismisses the checklist, it stays hidden for
    everyone. The "first question asked" CTA on the banner is per-user, but
    dismissal is a deliberate "we're done with this" signal from any admin
    or member, and re-showing it would be more annoying than helpful.
    """
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )
    await mark_dismissed(org_id=org_id)
