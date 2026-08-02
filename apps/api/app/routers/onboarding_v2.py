"""Onboarding v2 HR endpoints.

HR uses this surface to:
  * trigger an onboarding run (POST /onboarding/runs)
  * list & inspect runs
  * upload the signed-LOIPDF (POST /onboarding/runs/{id}/loi/upload-signed)
  * approve the AL+NDA bundle to send to the candidate
  * tag a KB document as a template (POST /onboarding/templates)
  * see template status (GET /onboarding/templates/status)

The public BGV form lives in `onboarding_public.py` (no auth — token in URL).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import inngest
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.models.onboarding_v2 import (
    BlockingFieldsResponse,
    DraftTextResponse,
    EditTextRequest,
    EditTextResponse,
    HrReferencesOverrideRequest,
    LOIApproveDraftResponse,
    OnboardingRunDetailRead,
    OnboardingRunRead,
    ReplaceDraftResponse,
    RunFieldValuesRequest,
    RunFieldValuesResponse,
    SourcesResponse,
    StartOnboardingRequest,
)
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.agents.onboarding_v2.pre_join import (
    ensure_pre_join_user,
    send_magic_link,
)
from app.services.documents import schema as doc_schema
from app.services.documents.constants import STATUS_CONFIRMED

log = logging.getLogger(__name__)

router = APIRouter(prefix="/onboarding", tags=["onboarding-v2"])


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


def _run_to_read(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "candidate_id": row.get("candidate_id"),
        "requisition_id": row.get("requisition_id"),
        "candidate_name": row["candidate_name"],
        "candidate_email": row["candidate_email"],
        "candidate_phone": row.get("candidate_phone"),
        "role_title": row["role_title"],
        "designation": row.get("designation"),
        "ctc_amount": row.get("ctc_amount"),
        "ctc_currency": row.get("ctc_currency"),
        "ctc_breakdown": row.get("ctc_breakdown"),
        "start_date": row["start_date"],
        "work_location": row.get("work_location"),
        "probation_period_months": row.get("probation_period_months"),
        "reporting_manager_name": row.get("reporting_manager_name"),
        "reporting_manager_email": row.get("reporting_manager_email"),
        "status": row.get("status") or "draft",
        "blocked_reason": row.get("blocked_reason"),
        "blocked_template_kind": row.get("blocked_template_kind"),
        "current_step": row.get("current_step"),
        "agent_run_id": row.get("agent_run_id"),
        "triggered_by_user_id": row.get("triggered_by_user_id"),
        "loi_sent_to_hr_at": row.get("loi_sent_to_hr_at"),
        "loi_signed_at": row.get("loi_signed_at"),
        "loi_sent_to_candidate_at": row.get("loi_sent_to_candidate_at"),
        "bgv_sent_at": row.get("bgv_sent_at"),
        "bgv_completed_at": row.get("bgv_completed_at"),
        "appointment_sent_at": row.get("appointment_sent_at"),
        "policies_assigned_at": row.get("policies_assigned_at"),
        "policies_acknowledged_at": row.get("policies_acknowledged_at"),
        "induction_sent_at": row.get("induction_sent_at"),
        "completed_at": row.get("completed_at"),
        "loi_approved_for_signing_at": row.get("loi_approved_for_signing_at"),
        "loi_draft_edited_at": row.get("loi_draft_edited_at"),
        "loi_draft_revision": row.get("loi_draft_revision") or 0,
        "references_form_expires_at": row.get("references_form_expires_at"),
        "references_submitted_at": row.get("references_submitted_at"),
        "references_reminder_count": row.get("references_reminder_count") or 0,
        "references_last_reminder_at": row.get("references_last_reminder_at"),
        "archived_at": row.get("archived_at"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ── Trigger ─────────────────────────────────────────────────────────────────


@router.post("/runs", response_model=OnboardingRunRead, status_code=status.HTTP_201_CREATED)
async def start_onboarding(
    body: StartOnboardingRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR clicks 'Mark Hired & Start Onboarding'. Creates the run + reference
    rows, fires the Inngest event, returns the run. The agent kicks off
    asynchronously."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    # Refuse if the candidate already has an active run (anti-double-click +
    # anti-foot-gun). Two onboarding runs for the same person would generate
    # conflicting LOIs.
    existing = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("id, status")
        .eq("org_id", org_id)
        .eq("candidate_email", body.candidate_email)
        .not_.in_("status", ["completed", "cancelled", "failed"])
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Active onboarding run already exists for {body.candidate_email}.",
        )

    run_payload = {
        "org_id": org_id,
        "candidate_id": str(body.candidate_id) if body.candidate_id else None,
        "requisition_id": str(body.requisition_id) if body.requisition_id else None,
        "candidate_name": body.candidate_name,
        "candidate_email": body.candidate_email,
        "candidate_phone": body.candidate_phone,
        "role_title": body.role_title,
        "designation": body.designation,
        "ctc_amount": body.ctc_amount,
        "ctc_currency": body.ctc_currency,
        "ctc_breakdown": body.ctc_breakdown,
        "start_date": body.start_date.isoformat(),
        "work_location": body.work_location,
        "probation_period_months": body.probation_period_months,
        "reporting_manager_name": body.reporting_manager_name,
        "reporting_manager_email": body.reporting_manager_email,
        "reporting_manager_user_id": (
            str(body.reporting_manager_user_id)
            if body.reporting_manager_user_id else None
        ),
        "status": "draft",
        "triggered_by_user_id": user_id,
    }

    def _insert_run() -> dict[str, Any]:
        res = svc.table("onboarding_runs").insert(run_payload).execute()
        return (res.data or [{}])[0]

    inserted = await asyncio.to_thread(_insert_run)
    run_id = inserted["id"]

    # Snapshot the org's step catalog onto the run before the agent is kicked,
    # so the run is pinned to the pipeline as it was configured at hire time —
    # not as it looks whenever the first Inngest worker happens to pick it up.
    await ob_catalog.materialize_run_steps(org_id=org_id, run_id=run_id)

    # Give the candidate an account now rather than when the LOI goes out.
    # Policy acknowledgement is signed-in only, and a policies step can be
    # positioned anywhere in the pipeline — so the account has to exist before
    # the first step that might need it, not partway down a sequence the org is
    # free to reorder. (Document collection no longer needs it: migration 112
    # gave that ask its own link.)
    #
    # Best-effort, exactly as it was at its old call site: a mail or auth blip
    # must not fail the hire. `ensure_pre_join_user` is idempotent, so the
    # policy step still retries this if it didn't take here.
    try:
        await ensure_pre_join_user(
            org_id=org_id,
            run_id=run_id,
            candidate_email=str(body.candidate_email),
            candidate_name=body.candidate_name,
        )
        # The sign-in link travels with the account for the same reason: any
        # step may be the one that asks the candidate for something, and a
        # portal they cannot sign into is not a portal.
        from app.config import get_settings

        settings = get_settings()
        await send_magic_link(
            email=str(body.candidate_email),
            redirect_to=(
                f"{settings.app_url.rstrip('/')}/candidate/onboarding"
                if settings.app_url
                else None
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "onboarding_v2.pre_join_provision_failed run=%s err=%s", run_id, exc
        )

    # Bulk-insert references.
    ref_rows = [
        {
            "org_id": org_id,
            "run_id": run_id,
            "reference_name": r.name,
            "reference_email": r.email,
            "reference_phone": r.phone,
            "relationship": r.relationship,
        }
        for r in body.references
    ]
    if ref_rows:
        await asyncio.to_thread(
            lambda: svc.table("onboarding_bgv_references").insert(ref_rows).execute()
        )

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="run_created",
        message=f"Onboarding started by HR for {body.candidate_name}.",
        actor_user_id=user_id,
    )

    # Fire async — the agent generates the LOIin the background.
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/start",
            data={
                "onboarding_run_id": run_id,
                "org_id": org_id,
                "triggered_by_user_id": user_id,
            },
        )
    )

    return _run_to_read(inserted)


# ── List + get ──────────────────────────────────────────────────────────────


@router.get("/runs")
async def list_runs(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)
    res = await asyncio.to_thread(
        lambda: client.table("onboarding_runs")
        .select("*")
        .eq("org_id", org_id)
        .is_("archived_at", "null")
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return {"runs": [_run_to_read(r) for r in (res.data or [])]}


# ── Sources: published jobs + their pipeline candidates ────────────────────


# Free-text ATS stage strings vary across providers — these prefixes are a
# coarse hint for the UI to highlight "this candidate looks hired" without
# blocking onboarding for anyone else. Matched case-insensitively.
_HIRED_STAGE_HINTS = (
    "hired",
    "offer accept",
    "offer-accept",
    "offer signed",
    "joined",
    "onboard",
)


def _looks_hired(stage: str | None) -> bool:
    if not stage:
        return False
    s = stage.lower()
    return any(h in s for h in _HIRED_STAGE_HINTS)


# Terminal onboarding statuses — runs in these states do NOT block starting a
# new run for the same recruiting candidate (e.g. cancelled → restart).
_TERMINAL_RUN_STATUSES = ("cancelled", "failed")


@router.get("/sources", response_model=SourcesResponse)
async def list_sources(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return published job requisitions with the recruiting candidates that
    have been synced from connected ATSes — the data the onboarding-page UI
    uses to let HR start an onboarding by picking a job and candidate.

    Each candidate is tagged with their current onboarding run id (if any
    non-terminal run exists) so the UI can show "Already onboarding" instead
    of a duplicate Start button. `looks_hired` is a soft hint based on the
    ATS stage string — not a hard filter, because stage taxonomies vary.
    """
    _user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    def _fetch_jobs() -> list[dict[str, Any]]:
        # Only published requisitions — drafts don't have ATS postings and
        # therefore no candidates to onboard. Cap at 200 to bound payload.
        res = (
            client.table("job_requisitions")
            .select(
                "id, role_request, location, department, seniority_level, "
                "published_at, candidates_last_synced_at, notion_tracker_url, "
                "status"
            )
            .eq("org_id", org_id)
            .in_("status", ["published", "Published"])
            .order("published_at", desc=True)
            .limit(200)
            .execute()
        )
        return res.data or []

    jobs = await asyncio.to_thread(_fetch_jobs)
    if not jobs:
        return {"jobs": []}

    job_ids = [j["id"] for j in jobs]

    def _fetch_candidates() -> list[dict[str, Any]]:
        res = (
            client.table("recruiting_candidates")
            .select(
                "id, requisition_id, full_name, email, phone, "
                "current_company, current_title, stage, resume_url, "
                "candidate_url, applied_at, ats_platform, updated_at"
            )
            .eq("org_id", org_id)
            .in_("requisition_id", job_ids)
            .order("applied_at", desc=True)
            .limit(2000)
            .execute()
        )
        return res.data or []

    def _fetch_runs() -> list[dict[str, Any]]:
        # Pull all non-terminal runs that link back to a recruiting candidate.
        # Using the service-equivalent user client (RLS scopes to the org).
        res = (
            client.table("onboarding_runs")
            .select("id, candidate_id, status")
            .eq("org_id", org_id)
            .not_.is_("candidate_id", "null")
            .not_.in_("status", list(_TERMINAL_RUN_STATUSES))
            .limit(2000)
            .execute()
        )
        return res.data or []

    candidates, runs = await asyncio.gather(
        asyncio.to_thread(_fetch_candidates),
        asyncio.to_thread(_fetch_runs),
    )

    # Index runs by candidate_id → latest-active row. If a candidate had
    # multiple non-terminal runs (shouldn't happen — guarded by the start
    # endpoint — but defensive), keep the most progressed one.
    run_by_candidate: dict[str, dict[str, Any]] = {}
    for r in runs:
        cid = r.get("candidate_id")
        if not cid:
            continue
        run_by_candidate[cid] = r

    by_job: dict[str, list[dict[str, Any]]] = {jid: [] for jid in job_ids}
    for c in candidates:
        run = run_by_candidate.get(c["id"])
        by_job.setdefault(c["requisition_id"], []).append(
            {
                "id": c["id"],
                "full_name": c.get("full_name"),
                "email": c.get("email"),
                "phone": c.get("phone"),
                "current_company": c.get("current_company"),
                "current_title": c.get("current_title"),
                "stage": c.get("stage"),
                "resume_url": c.get("resume_url"),
                "candidate_url": c.get("candidate_url"),
                "applied_at": c.get("applied_at"),
                "ats_platform": c.get("ats_platform"),
                "onboarding_run_id": run["id"] if run else None,
                "onboarding_status": run["status"] if run else None,
                "looks_hired": _looks_hired(c.get("stage")),
            }
        )

    return {
        "jobs": [
            {
                "id": j["id"],
                "role_request": j["role_request"],
                "location": j.get("location"),
                "department": j.get("department"),
                "seniority_level": j.get("seniority_level"),
                "published_at": j.get("published_at"),
                "candidates_last_synced_at": j.get("candidates_last_synced_at"),
                "notion_tracker_url": j.get("notion_tracker_url"),
                "candidates": by_job.get(j["id"], []),
            }
            for j in jobs
        ]
    }


@router.get("/runs/{run_id}", response_model=OnboardingRunDetailRead)
async def get_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    _user_id, org_id, token = _require_user(current_user)
    client = get_user_client(token)

    def _fetch_run() -> dict[str, Any] | None:
        res = (
            client.table("onboarding_runs")
            .select("*")
            .eq("id", run_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch_run)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Onboarding run not found.")

    def _fetch_related() -> tuple[list, list, list, dict[str, list]]:
        refs = (
            client.table("onboarding_bgv_references")
            .select("*")
            .eq("run_id", run_id)
            .order("created_at")
            .execute()
        )
        docs = (
            client.table("onboarding_documents")
            .select("*")
            .eq("run_id", run_id)
            .order("created_at")
            .execute()
        )
        events = (
            client.table("onboarding_events")
            .select("*")
            .eq("run_id", run_id)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        # Fetch per-signer statuses from signing envelopes. Keyed by our own
        # envelope_id so documents can be matched via esign_envelope_id.
        env_res = (
            client.table("onboarding_signing_envelopes")
            .select("envelope_id, signers, status")
            .eq("run_id", run_id)
            .execute()
        )
        signers_by_env: dict[str, list] = {}
        for e in (env_res.data or []):
            signers_by_env[e["envelope_id"]] = e.get("signers") or []
        return refs.data or [], docs.data or [], events.data or [], signers_by_env

    refs, docs, events, signers_by_env = await asyncio.to_thread(_fetch_related)

    # Re-mint signed URLs at request time so the page never serves a stale
    # link. Storage-stored signed_url is treated as a hint, not source of
    # truth — see services/agents/onboarding_v2/storage.py for the TTL
    # rationale (1h refresh vs 7d stored).
    fresh_urls = await ob_storage.refresh_signed_urls_for_run(run_id)

    detail = _run_to_read(row)
    # The pipeline this run is actually walking. Materialized rather than read
    # so a run created before the step engine still reports one.
    detail["steps"] = [
        {
            "id": s["id"],
            "step_key": s["step_key"],
            "kind": s["kind"],
            "label": s["label"],
            "document_type_key": s.get("document_type_key"),
            "bundle_key": s.get("bundle_key"),
            "bundle_label": s.get("bundle_label"),
            "position": s.get("position") or 0,
            "status": s.get("status") or "pending",
            "signer_roles": s.get("signer_roles") or [],
            "system_action": s.get("system_action"),
            "blocked_reason": s.get("blocked_reason"),
            "started_at": s.get("started_at"),
            "completed_at": s.get("completed_at"),
            # The approval gate: whether this step stops for HR once the
            # candidate has acted, and what HR said last time it did.
            "requires_hr_approval": bool(s.get("requires_hr_approval", False)),
            "review_decision": s.get("review_decision"),
            "review_note": s.get("review_note"),
            "reviewed_at": s.get("reviewed_at"),
            "approval_round": s.get("approval_round") or 0,
        }
        for s in await ob_catalog.materialize_run_steps(org_id=org_id, run_id=run_id)
    ]
    detail["references"] = [
        {
            "id": r["id"],
            "reference_name": r["reference_name"],
            "reference_email": r["reference_email"],
            "reference_phone": r.get("reference_phone"),
            "relationship": r.get("relationship"),
            "status": r.get("status") or "pending",
            "email_sent_at": r.get("email_sent_at"),
            "opened_at": r.get("opened_at"),
            "submitted_at": r.get("submitted_at"),
            "reminder_count": r.get("reminder_count") or 0,
            "response_worked_together_months": r.get("response_worked_together_months"),
            "response_would_recommend": r.get("response_would_recommend"),
            "response_strengths": r.get("response_strengths"),
            "response_concerns": r.get("response_concerns"),
            "response_role_description": r.get("response_role_description"),
        }
        for r in refs
    ]
    detail["documents"] = [
        {
            "id": d["id"],
            "kind": d["kind"],
            "storage_path": d["storage_path"],
            # Prefer the freshly-minted URL; only fall back to the stored
            # value if Supabase Storage was unreachable at refresh time.
            # When HR has uploaded an edited copy, surface that PDF instead
            # so the inline preview reflects the latest version.
            "signed_url": (
                fresh_urls.get(d.get("hr_edited_pdf_path"))
                or fresh_urls.get(d["signed_pdf_path"])
                or fresh_urls.get(d["storage_path"])
                or d.get("signed_url")
            ),
            # The executed copy, specifically. `signed_url` above prefers the
            # HR-edited draft so the review panel previews the latest wording;
            # the documents archive wants the countersigned artifact and
            # nothing else, so it gets its own link rather than a preference
            # order it would have to argue with.
            "signed_pdf_url": (
                fresh_urls.get(d["signed_pdf_path"])
                if d.get("signed_pdf_path")
                else None
            ),
            "sign_status": d.get("sign_status") or "draft",
            "signed_pdf_path": d.get("signed_pdf_path"),
            "signed_uploaded_at": d.get("signed_uploaded_at"),
            "file_bytes": d.get("file_bytes"),
            "hr_edited_storage_path": d.get("hr_edited_storage_path"),
            "hr_edited_pdf_path": d.get("hr_edited_pdf_path"),
            "hr_edited_at": d.get("hr_edited_at"),
            "hr_edit_revision": d.get("hr_edit_revision") or 0,
            "esign_envelope_id": d.get("esign_envelope_id"),
            "esign_status": d.get("esign_status"),
            "esign_signing_url": d.get("esign_signing_url"),
            "esign_completed_at": d.get("esign_completed_at"),
            "esign_signers": [
                {
                    "role": s.get("role", ""),
                    "name": s.get("name", ""),
                    "status": s.get("status", "pending"),
                    "completed_at": s.get("completed_at"),
                }
                for s in signers_by_env.get(d.get("esign_envelope_id") or "", [])
            ],
            "created_at": d["created_at"],
            "updated_at": d["updated_at"],
        }
        for d in docs
    ]
    detail["events"] = [
        {
            "id": e["id"],
            "actor_kind": e["actor_kind"],
            "event_type": e["event_type"],
            "message": e.get("message"),
            "metadata": e.get("metadata"),
            "created_at": e["created_at"],
        }
        for e in events
    ]
    return detail


# ── Legacy per-document endpoints ──────────────────────────────────────────
# Superseded by the step-keyed routes in `onboarding_steps.py`, which read who
# signs from the step's `signer_roles` instead of hardcoding it per document.
# Kept because the frontend and the Chrome extension still call them.


@router.post("/runs/{run_id}/loi/upload-signed")
async def upload_signed_loi(
    run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    from app.routers.onboarding_steps import upload_signed_step

    step = await _loi_step(run_id)
    return await upload_signed_step(
        run_id=run_id,
        step_key=step["step_key"],
        file=file,
        current_user=current_user,
    )


# ── Approve AL+NDA bundle ───────────────────────────────────────────────────


@router.post("/runs/{run_id}/offer-bundle/approve")
async def approve_offer_bundle(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Legacy alias. The bundle is now approved by the key of its lead step."""
    from app.routers.onboarding_steps import approve_step

    return await approve_step(
        run_id=run_id, step_key="appointment_letter", current_user=current_user
    )


async def _loi_step(run_id: str) -> dict[str, Any]:
    """The step that renders this run's letter of intent.

    These endpoints were written when the LOI was step `loi` and nothing else
    could be, so they addressed it by that literal everywhere — the document
    row's `kind`, the envelope's `document_kinds`, the run status. A catalog an
    org renamed or rebuilt renders the same letter under its own key, so it is
    found by document type and its key read off the step.
    """
    steps = await ob_catalog.get_run_steps(run_id)
    step = ob_catalog.find_document_step(steps, ob_catalog.DOCUMENT_TYPE_LOI)
    if step is None:
        # A run that predates `document_type_key` being populated, or one whose
        # step really is the seeded default.
        step = next((s for s in steps if s["step_key"] == "loi"), None)
    if step is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This run's pipeline has no letter-of-intent step.",
        )
    return step


@router.get(
    "/runs/{run_id}/loi/draft-text",
    response_model=DraftTextResponse,
)
async def get_loi_draft_text(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> DraftTextResponse:
    """Legacy alias. Every generated draft is editable by step key now."""
    from app.routers.onboarding_steps import get_step_draft_text

    step = await _loi_step(run_id)
    return await get_step_draft_text(
        run_id=run_id, step_key=step["step_key"], current_user=current_user
    )


@router.post(
    "/runs/{run_id}/loi/edit-text",
    response_model=EditTextResponse,
)
async def edit_loi_draft_text(
    run_id: str,
    body: EditTextRequest,
    current_user: dict = Depends(verify_jwt),
) -> EditTextResponse:
    """Legacy alias for editing the LOI draft in place."""
    from app.routers.onboarding_steps import edit_step_draft_text

    step = await _loi_step(run_id)
    return await edit_step_draft_text(
        run_id=run_id,
        step_key=step["step_key"],
        body=body,
        current_user=current_user,
    )


@router.post(
    "/runs/{run_id}/loi/replace-draft",
    response_model=ReplaceDraftResponse,
)
async def replace_loi_draft(
    run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> ReplaceDraftResponse:
    """Legacy alias for swapping in a .docx edited in Word."""
    from app.routers.onboarding_steps import replace_step_draft

    step = await _loi_step(run_id)
    return await replace_step_draft(
        run_id=run_id,
        step_key=step["step_key"],
        file=file,
        current_user=current_user,
    )


@router.post(
    "/runs/{run_id}/loi/approve-draft",
    response_model=LOIApproveDraftResponse,
)
async def approve_loi_draft(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Legacy alias for approving the LOI draft.

    The routing it used to hardcode — HR first, then the candidate — is now the
    `signer_roles` the LOI step is seeded with, so an org that wants it signed
    differently changes it in Settings rather than needing a code change here.
    """
    from app.routers.onboarding_steps import approve_step

    step = await _loi_step(run_id)
    result = await approve_step(
        run_id=run_id, step_key=step["step_key"], current_user=current_user
    )
    # The old response named the run status; callers branch on it to decide
    # whether to show the signing panel or the print-and-scan prompt.
    legacy = {
        "pending_signature": "loi_pending_esign_signature",
        "awaiting_manual_signature": "loi_pending_hr_sign",
        "sending": "loi_signed_uploaded",
    }.get(result["status"], result["status"])
    return {"status": legacy, "document_id": None}


@router.get("/runs/{run_id}/loi/signing-url")
async def get_loi_signing_url(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Legacy alias for HR's signing link on the LOI envelope."""
    from app.routers.onboarding_steps import get_step_signing_url

    step = await _loi_step(run_id)
    return await get_step_signing_url(
        run_id=run_id, step_key=step["step_key"], current_user=current_user
    )


# ── HR override: enter references manually if the candidate ghosts ─────────


@router.post("/runs/{run_id}/references-override")
async def references_override(
    run_id: str,
    body: HrReferencesOverrideRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR-side fallback: if the candidate hasn't filled the references form,
    HR can type the refs in themselves. Behaves identically to the public
    candidate submission — writes onboarding_bgv_references rows, stamps
    references_submitted_at, and kicks the agent into bgv_pending."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("id, status, references_submitted_at")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    # Only meaningful while the run is parked waiting. Accepting after that
    # would create dangling refs not tied to the BGV flow.
    if run.data["status"] not in (
        "loi_sent_to_candidate",
        "awaiting_candidate_references",
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{run.data['status']}' — references can only be "
            "overridden while awaiting candidate input.",
        )
    if run.data.get("references_submitted_at"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "References were already submitted for this run.",
        )

    ref_rows = [
        {
            "org_id": org_id,
            "run_id": run_id,
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
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="references_override_submitted",
        message=(
            f"HR submitted {len(body.references)} reference(s) on the "
            "candidate's behalf (override)."
        ),
        actor_user_id=user_id,
        metadata={"count": len(body.references)},
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "ok", "count": len(body.references)}


# ── HR manual candidate nudge ────────────────────────────────────────────────


@router.post("/runs/{run_id}/references-nudge")
async def nudge_candidate_references(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR manually sends an immediate reminder to the candidate to submit
    references. Bypasses the 3-reminder auto-cron cap."""
    from app.config import get_settings
    from app.services.email import send_email_event

    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select(
            "id, status, candidate_name, candidate_email, role_title, "
            "references_form_token, references_form_expires_at, references_reminder_count"
        )
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    r = run.data
    if r["status"] not in ("awaiting_candidate_references", "loi_sent_to_candidate"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{r['status']}' — nudge only works while awaiting references.",
        )
    if not r.get("references_form_token"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No references form token on this run."
        )

    if r.get("references_form_expires_at"):
        try:
            exp = datetime.fromisoformat(
                r["references_form_expires_at"].replace("Z", "+00:00")
            )
            if exp < datetime.now(UTC):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "The references form link has expired. Extend the token first, then nudge.",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    settings = get_settings()
    form_url = (
        f"{settings.app_url.rstrip('/')}/references/{r['references_form_token']}"
        if settings.app_url
        else None
    )
    if not form_url:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "APP_URL not configured."
        )

    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    company_name = (org_row.data or {}).get("name", "the hiring team")
    reminder_count = (r.get("references_reminder_count") or 0) + 1
    now = datetime.now(UTC)

    await send_email_event(
        event_type="onboarding_candidate_refs_reminder",
        to=r["candidate_email"],
        user_id=None,
        org_id=org_id,
        dedupe_key=f"cand-refs-rem-{run_id}-hr-{reminder_count}",
        data={
            "candidate_name": r["candidate_name"],
            "company_name": company_name,
            "role_title": r.get("role_title") or "",
            "form_url": form_url,
        },
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update(
            {
                "references_reminder_count": reminder_count,
                "references_last_reminder_at": now.isoformat(),
            }
        )
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="candidate_references_nudged",
        message=(
            f"HR sent a manual reminder to {r['candidate_email']} to submit references "
            f"(reminder #{reminder_count})."
        ),
        actor_user_id=user_id,
        metadata={"reminder_count": reminder_count},
    )
    return {"status": "ok", "reminder_count": reminder_count}


# ── Extend expired references form token ────────────────────────────────────


@router.post("/runs/{run_id}/references-token/extend")
async def extend_references_token(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Regenerate the candidate references form token and set a fresh 14-day
    expiry. Also re-emails the candidate with the new link."""
    import uuid
    from datetime import timedelta

    from app.config import get_settings
    from app.services.email import send_email_event

    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select(
            "id, status, candidate_name, candidate_email, role_title, "
            "references_reminder_count"
        )
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    r = run.data
    if r["status"] not in ("awaiting_candidate_references", "loi_sent_to_candidate"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{r['status']}' — token can only be extended while awaiting references.",
        )

    new_token = str(uuid.uuid4())
    new_expiry = (datetime.now(UTC) + timedelta(days=14)).isoformat()

    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update(
            {
                "references_form_token": new_token,
                "references_form_expires_at": new_expiry,
            }
        )
        .eq("id", run_id)
        .execute()
    )

    settings = get_settings()
    form_url = (
        f"{settings.app_url.rstrip('/')}/references/{new_token}"
        if settings.app_url
        else None
    )
    org_row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    company_name = (org_row.data or {}).get("name", "the hiring team")
    now = datetime.now(UTC)
    reminder_count = (r.get("references_reminder_count") or 0) + 1

    if form_url:
        await send_email_event(
            event_type="onboarding_candidate_refs_reminder",
            to=r["candidate_email"],
            user_id=None,
            org_id=org_id,
            dedupe_key=f"cand-refs-ext-{run_id}-{new_token[:8]}",
            data={
                "candidate_name": r["candidate_name"],
                "company_name": company_name,
                "role_title": r.get("role_title") or "",
                "form_url": form_url,
            },
        )
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update(
                {
                    "references_reminder_count": reminder_count,
                    "references_last_reminder_at": now.isoformat(),
                }
            )
            .eq("id", run_id)
            .execute()
        )

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="references_token_extended",
        message=(
            f"HR extended the references form link for {r['candidate_email']} "
            f"(new expiry: {new_expiry[:10]}). New link emailed to candidate."
        ),
        actor_user_id=user_id,
        metadata={"new_expiry": new_expiry},
    )
    return {"status": "ok", "new_expiry": new_expiry}


# ── Cancel / resume ─────────────────────────────────────────────────────────


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Cancel an active onboarding run.

    Side effects (in addition to flipping the run status):
      * All non-submitted BGV reference tokens are expired so a leaked link
        can't be used after we've told the candidate the offer is off the
        table.
      * Any open signing envelope is voided. We do this best-effort — if
        the apps/esign API call fails (network, deploy restart) we still
        flip the local row and log the failure; HR can void manually later.
    """
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()

    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update(
            {
                "status": "cancelled",
                "cancelled_at": now,
                "cancelled_by_user_id": user_id,
            }
        )
        .eq("id", run_id)
        .eq("org_id", org_id)
        .not_.in_("status", ["completed", "cancelled"])
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found or already terminal.")

    # Expire BGV tokens so an in-the-wild link no longer works. We don't
    # expire ones that already submitted — we want to keep the response.
    expired_ref_count = await asyncio.to_thread(
        lambda: len(
            (
                svc.table("onboarding_bgv_references")
                .update({"status": "expired"})
                .eq("run_id", run_id)
                .in_("status", ["pending", "sent", "opened"])
                .execute()
            ).data or []
        )
    )

    # Best-effort: void any open signing envelopes.
    try:
        from app.services.integrations.inhouse_sign import void_envelopes_for_run
        voided = await void_envelopes_for_run(
            org_id=org_id, run_id=run_id, reason="Onboarding run cancelled by HR.",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.cancel_void_envelopes_failed run=%s err=%s", run_id, exc)
        voided = 0

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="run_cancelled",
        message="HR cancelled the onboarding.",
        actor_user_id=user_id,
        metadata={
            "expired_references": expired_ref_count,
            "voided_envelopes": voided,
        },
    )
    return {
        "status": "cancelled",
        "expired_references": expired_ref_count,
        "voided_envelopes": voided,
    }


@router.post("/runs/{run_id}/archive")
async def archive_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Hide a run from the default listing without destroying it — the
    "Delete" action in the UI. Works on runs in any status, including active
    ones; unlike cancel it doesn't touch BGV tokens or signing envelopes,
    since the row and its linked documents/audit trail stay intact."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    now = datetime.now(UTC).isoformat()

    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"archived_at": now, "archived_by": user_id})
        .eq("id", run_id)
        .eq("org_id", org_id)
        .is_("archived_at", "null")
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found or already deleted.")

    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="run_archived",
        message="HR deleted the onboarding from the list.",
        actor_user_id=user_id,
    )
    return {"archived_at": now}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Force the agent to inspect the run and dispatch again. Used by the
    'Retry' button on the dashboard for blocked / failed runs."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    # If the run is in a hard-failed state, reset it to draft so the agent
    # won't short-circuit on the terminal-state guard.
    run_row = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("status")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run_row.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Run not found.")
    if run_row.data["status"] == "failed":
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update({"status": "draft", "blocked_reason": None})
            .eq("id", run_id)
            .eq("org_id", org_id)
            .execute()
        )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "ok"}


# ── Blocked on a missing value ──────────────────────────────────────────────
#
# A run blocks when a required template field has no value — "Company Address
# is required but has no value." Every other block (no template, no confirmed
# fields, template drift) is fixed in the template library, but this one is a
# missing piece of data, and the fix is HR typing it. These two endpoints back
# the form that lets them, rather than sending them off to populate whichever
# table the field happens to map to.


def _fetch_run(svc, run_id: str, org_id: str) -> dict[str, Any]:
    res = (
        svc.table("onboarding_runs")
        .select("id, status, blocked_template_kind, field_values")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    return res.data


@router.get("/runs/{run_id}/blocking-fields", response_model=BlockingFieldsResponse)
async def get_blocking_fields(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """The fields HR must supply before this run's document can be generated.

    Read off the last generation attempt's `validation_report` rather than
    re-derived here: that report is what actually blocked the run, so answering
    it is guaranteed to unblock it. If the newest attempt did not fail
    validation — no template at all, or a later attempt succeeded — there is
    nothing to type and the list comes back empty.
    """
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(lambda: _fetch_run(svc, run_id, org_id))
    entered = run.get("field_values") or {}

    latest = await asyncio.to_thread(
        lambda: svc.table("generated_documents")
        .select("id, status, version_id, validation_report, context_snapshot, doc_templates(name)")
        .eq("org_id", org_id)
        .eq("onboarding_run_id", run_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    row = (latest.data or [None])[0]
    kind = run.get("blocked_template_kind")
    if not row or row.get("status") != "validation_failed":
        return {"document_kind": kind, "fields": []}

    report = row.get("validation_report") or {}
    context = (row.get("context_snapshot") or {}).get("values") or {}

    variables = {
        v["internal_name"]: v
        for v in await doc_schema.list_variables(
            org_id=org_id,
            version_id=row["version_id"],
            statuses=(STATUS_CONFIRMED,),
        )
    }

    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in report.get("errors") or []:
        name = issue.get("variable")
        # A slot-level error (an unmapped position, an unknown variable) names
        # a template problem, not a value HR can type. Those still belong in
        # `blocked_reason`, which the UI shows above this form.
        if not name or name in seen or name not in variables:
            continue
        seen.add(name)
        variable = variables[name]
        prefill = entered.get(name)
        if prefill in (None, ""):
            # Only for a value that failed a *format* check: a missing one has
            # nothing useful to show, and defaults would look like real input.
            prefill = (
                context.get(name)
                if issue.get("code") != "missing_required"
                else ""
            )
        fields.append({
            "internal_name": name,
            "label": variable.get("display_name") or name.replace("_", " ").title(),
            "data_type": variable.get("data_type") or "text",
            "description": variable.get("description"),
            "example_value": variable.get("example_value"),
            "code": issue.get("code") or "missing_required",
            "message": issue.get("message") or "",
            "value": str(prefill or ""),
        })

    return {
        "document_kind": kind,
        "template_name": (row.get("doc_templates") or {}).get("name"),
        "generated_document_id": row.get("id"),
        "fields": fields,
    }


@router.post("/runs/{run_id}/field-values", response_model=RunFieldValuesResponse)
async def save_field_values(
    run_id: str,
    body: RunFieldValuesRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Store HR's answers on the run and re-drive the agent.

    Saving and resuming are one action on purpose: HR filled the form to
    unblock the run, and a saved value that sits there until someone
    remembers to press Re-run is the same stuck run with extra steps.
    """
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(lambda: _fetch_run(svc, run_id, org_id))
    if run.get("status") in ("completed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This run is closed — its documents can no longer be regenerated.",
        )

    merged = dict(run.get("field_values") or {})
    for name, value in body.values.items():
        cleaned = value.strip()
        if cleaned:
            merged[name] = cleaned
        else:
            merged.pop(name, None)

    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"field_values": merged})
        .eq("id", run_id)
        .eq("org_id", org_id)
        .execute()
    )

    # The values themselves stay out of the event: a filled field can be a
    # salary or a home address, and the timeline is read by anyone with access
    # to the run. The field names are enough to explain what changed.
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="fields_filled",
        message=(
            "HR supplied "
            + ", ".join(sorted(body.values.keys())[:6])
            + " for the blocked document."
        ),
        actor_user_id=user_id,
        metadata={"fields": sorted(body.values.keys())},
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "ok", "saved": len(merged), "resumed": True}


# ── Templates ───────────────────────────────────────────────────────────────
#
# The template library, field detection, and the slots review loop used to
# live here. They now live in `routers/document_templates.py`, backed by
# `services/documents` and the tables added in migration 099 — templates are
# a first-class entity with versions and typed fields rather than a
# `documents` row wearing a `template_kind` column.
#
# The agent reaches templates through `services/documents/generation`, so
# nothing in this router needs to know about them any more.


@router.get("/runs/{run_id}/loi/docx-url")
async def get_loi_docx_url(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Legacy alias for downloading the LOI's current .docx."""
    from app.routers.onboarding_steps import get_step_docx_url

    step = await _loi_step(run_id)
    return await get_step_docx_url(
        run_id=run_id, step_key=step["step_key"], current_user=current_user
    )
