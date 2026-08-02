"""Public Onboarding endpoints — no JWT auth.

Three forms live here, all filled by someone who should not need an account to
fill them: the BGV reference form (a referee, who has no relationship with us
at all), the candidate's own list of referees, and the candidate's document
upload checklist. The last one used to require a login — the candidate had one,
provisioned at run creation, but reaching it meant still having the magic-link
email from weeks earlier, which is exactly the email people lose.

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
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.models.onboarding_catalog import PublicCandidateDocumentsRead
from app.models.onboarding_v2 import (
    BgvFormPrefill,
    BgvFormSubmit,
    CandidateReferencesPrefill,
    CandidateReferencesSubmit,
)

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


# ── Candidate references form ─────────────────────────────────────────────
#
# The candidate fills this AFTER they receive their LOI. The token lives in
# the URL exactly like the BGV referee tokens — same security posture (UUID,
# expiry, single-submission). The only difference: this form is filled by
# the *candidate*, and submitting it inserts the BGV reference rows that the
# agent's _step_kick_off_bgv reads to fire the per-reference verification
# emails.


async def _load_run_by_refs_token(token: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select(
            "id, org_id, status, candidate_name, role_title, "
            "references_form_expires_at, references_submitted_at"
        )
        .eq("references_form_token", token)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


@router.get(
    "/references/{token}", response_model=CandidateReferencesPrefill
)
async def get_candidate_references_prefill(token: str) -> dict[str, Any]:
    token = _validate_token(token)
    row = await _load_run_by_refs_token(token)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not recognised.")

    expires_at_iso = row.get("references_form_expires_at")
    if expires_at_iso:
        try:
            expires_at = datetime.fromisoformat(
                expires_at_iso.replace("Z", "+00:00")
            )
        except ValueError:
            expires_at = datetime.now(UTC)
        if expires_at < datetime.now(UTC):
            raise HTTPException(status.HTTP_410_GONE, detail="Link expired.")
    else:
        expires_at = datetime.now(UTC)

    if row.get("status") in ("cancelled", "failed"):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This onboarding has been cancelled.",
        )

    svc = get_service_client()
    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name, required_references_count")
        .eq("id", row["org_id"])
        .maybe_single()
        .execute()
    )
    org_data = org.data or {}
    return {
        "candidate_name": row.get("candidate_name") or "",
        "company_name": org_data.get("name") or "the hiring company",
        "role_title": row.get("role_title") or "",
        "required_count": int(org_data.get("required_references_count") or 2),
        "expires_at": expires_at.isoformat(),
        "already_submitted": bool(row.get("references_submitted_at")),
    }


async def _reopen_bgv_review(run_id: str) -> None:
    """Clear the HR approval on this run's background-verification step.

    Best-effort. A run whose BGV step has no approval to clear — the common
    case, a first submission — is a no-op, and a failure here must not lose
    references the candidate just spent ten minutes typing.
    """
    from app.services.agents.onboarding_v2 import catalog as ob_catalog

    try:
        steps = await ob_catalog.get_run_steps(run_id)
        for step in steps:
            if step.get("system_action") == ob_catalog.SYSTEM_ACTION_BGV:
                await ob_catalog.clear_step_review(step["id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_public.bgv_review_reset_failed err=%s", exc)


@router.post("/references/{token}")
async def submit_candidate_references(
    token: str, body: CandidateReferencesSubmit
) -> dict[str, Any]:
    token = _validate_token(token)
    row = await _load_run_by_refs_token(token)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not recognised.")
    if row.get("references_submitted_at"):
        return {"status": "already_submitted"}
    if row.get("status") in ("cancelled", "failed"):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This onboarding has been cancelled.",
        )

    expires_at_iso = row.get("references_form_expires_at")
    if expires_at_iso:
        try:
            expires_at = datetime.fromisoformat(
                expires_at_iso.replace("Z", "+00:00")
            )
            if expires_at < datetime.now(UTC):
                raise HTTPException(status.HTTP_410_GONE, detail="Link expired.")
        except ValueError:
            pass

    svc = get_service_client()
    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("required_references_count")
        .eq("id", row["org_id"])
        .maybe_single()
        .execute()
    )
    required = int((org.data or {}).get("required_references_count") or 2)
    if len(body.references) < required:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Please provide at least {required} reference(s); "
                f"received {len(body.references)}."
            ),
        )

    ref_rows = [
        {
            "org_id": row["org_id"],
            "run_id": row["id"],
            "reference_name": r.name,
            "reference_email": r.email,
            "reference_phone": r.phone,
            "relationship": r.relationship,
        }
        for r in body.references
    ]
    await asyncio.to_thread(
        lambda: svc.table("onboarding_bgv_references").insert(ref_rows).execute()
    )

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"references_submitted_at": now})
        .eq("id", row["id"])
        .execute()
    )

    # A second list, submitted because HR sent the first one back, is work they
    # have not seen. Withdrawing the step's approval is what closes the gate
    # again behind it — otherwise the agent would read the earlier approval as
    # covering referees nobody has looked at.
    await _reopen_bgv_review(row["id"])

    try:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_events").insert(
                {
                    "org_id": row["org_id"],
                    "run_id": row["id"],
                    "actor_kind": "candidate",
                    "event_type": "candidate_references_submitted",
                    "message": (
                        f"Candidate submitted {len(body.references)} "
                        "background-check reference(s)."
                    ),
                    "metadata": {"count": len(body.references)},
                }
            ).execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_public.refs_event_log_failed err=%s", exc)

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={
                "onboarding_run_id": row["id"],
                "org_id": row["org_id"],
            },
        )
    )
    return {"status": "submitted", "count": len(body.references)}


# ── Candidate document upload ─────────────────────────────────────────────
#
# The checklist a `collect` step asks for, and a place to answer it, without a
# login. Same token shape as the two forms above and the same expiry handling;
# what differs is that this one is not single-use — a candidate comes back to a
# document ask over days, replaces a scan HR rejected, and is asked for a second
# batch later in the pipeline, all on the one link.
#
# What may be uploaded, and when, is not decided here: both endpoints delegate
# to `onboarding_catalog`, which serves the signed-in version of this same
# screen. A step the run hasn't reached is refused identically on both paths.


async def _load_run_by_documents_token(token: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("*")
        .eq("documents_token", token)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


async def _open_documents_run(token: str) -> tuple[dict[str, Any], datetime | None]:
    """The run behind a document link, or the reason it can't be opened.

    Expiry is checked here rather than in each endpoint so a link that has
    lapsed cannot still be uploaded against by someone who kept the tab open.
    """
    token = _validate_token(token)
    row = await _load_run_by_documents_token(token)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Link not recognised.")

    if row.get("status") in ("cancelled", "failed"):
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="This onboarding has been cancelled by the hiring company.",
        )

    expires_at: datetime | None = None
    expires_at_iso = row.get("documents_token_expires_at")
    if expires_at_iso:
        try:
            expires_at = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
        except ValueError:
            expires_at = None
        if expires_at and expires_at < datetime.now(UTC):
            raise HTTPException(
                status.HTTP_410_GONE,
                detail=(
                    "This upload link has expired. Ask your HR contact to send "
                    "you a fresh one."
                ),
            )
    return row, expires_at


@router.get("/documents/{token}", response_model=PublicCandidateDocumentsRead)
async def get_candidate_documents(token: str) -> PublicCandidateDocumentsRead:
    """The checklist this candidate has been asked for, and what's already in."""
    from app.routers.onboarding_catalog import build_candidate_view

    row, expires_at = await _open_documents_run(token)
    view = await build_candidate_view(row)
    return PublicCandidateDocumentsRead(
        **view.model_dump(),
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@router.post("/documents/{token}/steps/{step_key}/items/{item_key}")
async def upload_candidate_document_public(
    token: str,
    step_key: str,
    item_key: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """File one document against one checklist item, on the link alone."""
    from app.routers.onboarding_catalog import store_candidate_upload

    row, _expires_at = await _open_documents_run(token)
    return await store_candidate_upload(
        run=row,
        step_key=step_key,
        item_key=item_key,
        file=file,
        # No session, so no user to attribute this to. The run is the candidate.
        submitted_by=None,
    )
