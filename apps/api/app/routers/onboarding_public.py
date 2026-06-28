"""Public Onboarding endpoints — no JWT auth.

The BGV reference form needs to be accessible to people outside the org
(literally anyone the candidate listed as a reference). We can't sit them
behind a Supabase login because they don't have an account.

The credential lives in the URL as `/bgv/<UUID>`. The token is single-use
in spirit (we mark `submitted` and the form refuses replay) and time-bounded
(14 day expiry from `onboarding_bgv_references.token_expires_at`).

Security posture:
  * Tokens are random v4 UUIDs (122 bits of entropy) — uniformly distributed
    over 2^122, so brute-force enumeration is infeasible.
  * No PII is exposed beyond what's strictly needed: reference name,
    candidate name + role, and company name. We never echo the candidate's
    email, phone, or salary.
  * Token expiry is enforced on read AND write — both endpoints check.
  * Rate-limit upstream via the request-context middleware (per-IP) so a
    leak doesn't enable mass submission.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import inngest
from fastapi import APIRouter, HTTPException, status

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.models.onboarding_v2 import BgvFormPrefill, BgvFormSubmit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding/public", tags=["onboarding-public"])


def _validate_token(token: str) -> str:
    try:
        return str(uuid.UUID(token))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Malformed token."
        ) from exc


async def _load_token_row(token: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_bgv_references")
        .select("*")
        .eq("token", token)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


@router.get("/bgv/{token}", response_model=BgvFormPrefill)
async def get_bgv_prefill(token: str) -> dict[str, Any]:
    token = _validate_token(token)
    row = await _load_token_row(token)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not recognised.")

    expires_at_iso = row.get("token_expires_at")
    if expires_at_iso:
        try:
            expires_at = datetime.fromisoformat(
                expires_at_iso.replace("Z", "+00:00")
            )
        except ValueError:
            expires_at = datetime.now(UTC)
        if expires_at < datetime.now(UTC):
            # Mark expired so the next admin pull reflects it.
            svc = get_service_client()
            await asyncio.to_thread(
                lambda: svc.table("onboarding_bgv_references")
                .update({"status": "expired"})
                .eq("id", row["id"])
                .execute()
            )
            raise HTTPException(status.HTTP_410_GONE, detail="Link expired.")
    else:
        expires_at = datetime.now(UTC)

    # Mark opened (one-time, idempotent — don't downgrade `submitted` back).
    if row.get("status") == "sent":
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("onboarding_bgv_references")
            .update(
                {
                    "status": "opened",
                    "opened_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", row["id"])
            .execute()
        )

    # Pull run + org for the prefill context.
    svc = get_service_client()
    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("candidate_name, role_title, org_id")
        .eq("id", row["run_id"])
        .maybe_single()
        .execute()
    )
    candidate_name = (run.data or {}).get("candidate_name") or "the candidate"
    role_title = (run.data or {}).get("role_title") or "the role"

    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", row["org_id"])
        .maybe_single()
        .execute()
    )
    org_name = (org.data or {}).get("name") or "the hiring company"

    return {
        "reference_name": row["reference_name"],
        "candidate_name": candidate_name,
        "candidate_role": role_title,
        "company_name": org_name,
        "expires_at": expires_at.isoformat(),
        "already_submitted": row.get("status") == "submitted",
    }


@router.post("/bgv/{token}")
async def submit_bgv(token: str, body: BgvFormSubmit) -> dict[str, Any]:
    token = _validate_token(token)
    row = await _load_token_row(token)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not recognised.")
    if row.get("status") == "submitted":
        # Friendly idempotent: just acknowledge again.
        return {"status": "already_submitted"}
    if row.get("status") == "expired":
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This reference check has been cancelled by the hiring company.",
        )

    expires_at_iso = row.get("token_expires_at")
    if expires_at_iso:
        try:
            expires_at = datetime.fromisoformat(
                expires_at_iso.replace("Z", "+00:00")
            )
            if expires_at < datetime.now(UTC):
                raise HTTPException(status.HTTP_410_GONE, detail="Link expired.")
        except ValueError:
            pass

    # Hard-check the run isn't cancelled — defensive: even if the token
    # status row wasn't flipped (e.g. crash mid-cancel), don't accept a
    # response for a terminated run.
    svc = get_service_client()
    run_status_row = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("status")
        .eq("id", row["run_id"])
        .maybe_single()
        .execute()
    )
    run_status = (run_status_row.data or {}).get("status") if run_status_row else None
    if run_status in ("cancelled", "failed"):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This reference check has been cancelled by the hiring company.",
        )

    svc = get_service_client()
    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_bgv_references")
        .update(
            {
                "status": "submitted",
                "submitted_at": now,
                "response_worked_together_months": body.worked_together_months,
                "response_would_recommend": body.would_recommend,
                "response_strengths": body.strengths.strip() or None,
                "response_concerns": body.concerns.strip() or None,
                "response_role_description": body.role_description.strip() or None,
            }
        )
        .eq("id", row["id"])
        .execute()
    )

    # Fire-and-forget audit + agent kick.
    try:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_events").insert(
                {
                    "org_id": row["org_id"],
                    "run_id": row["run_id"],
                    "actor_kind": "reference",
                    "event_type": "bgv_response_submitted",
                    "message": f"Reference {row['reference_name']} responded.",
                    "metadata": {
                        "reference_id": row["id"],
                        "would_recommend": body.would_recommend,
                    },
                }
            ).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_public.event_log_failed err=%s", exc)

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/bgv_response",
            data={
                "onboarding_run_id": row["run_id"],
                "org_id": row["org_id"],
            },
        )
    )
    return {"status": "submitted"}
