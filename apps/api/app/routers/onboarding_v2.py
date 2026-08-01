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
    HrReferencesOverrideRequest,
    LOIApproveDraftResponse,
    LOIDraftParagraph,
    LOIDraftTextResponse,
    LOIEditTextRequest,
    LOIEditTextResponse,
    LOIReplaceDraftResponse,
    OnboardingRunDetailRead,
    OnboardingRunRead,
    RunFieldValuesRequest,
    RunFieldValuesResponse,
    SourcesResponse,
    StartOnboardingRequest,
)
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.agents.onboarding_v2.pre_join import ensure_pre_join_user
from app.services.documents import schema as doc_schema
from app.services.documents import text_edit
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
    # A document-collection step can be positioned anywhere in the pipeline,
    # and the portal it sends them to is authenticated — so the account has to
    # exist before the first step that might ask them for something, not
    # partway down a sequence the org is free to reorder.
    #
    # Best-effort, exactly as it was at its old call site: a mail or auth blip
    # must not fail the hire. `ensure_pre_join_user` is idempotent, so the
    # LOI step still retries this if it didn't take here.
    try:
        await ensure_pre_join_user(
            org_id=org_id,
            run_id=run_id,
            candidate_email=str(body.candidate_email),
            candidate_name=body.candidate_name,
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


# ── Upload signed LOI───────────────────────────────────────────────────────


@router.post("/runs/{run_id}/loi/upload-signed")
async def upload_signed_loi(
    run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("id, status, candidate_name")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if run.data["status"] != "loi_pending_hr_sign":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{run.data['status']}', expected 'loi_pending_hr_sign'.",
        )

    body = await file.read()
    if not body or not body.startswith(b"%PDF"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Upload must be a PDF file."
        )
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "PDF too large (>25MB)."
        )

    # Soft sanity checks: page count + "did HR actually sign it?" heuristic.
    # We don't reject on these — they're advisory warnings returned in the
    # response so the UI can show a "Looks like you forgot to sign" prompt.
    warnings: list[str] = []
    try:
        import pymupdf  # type: ignore[import-not-found]
        with pymupdf.open(stream=body, filetype="pdf") as pdf:
            if pdf.page_count < 1:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "PDF has no pages."
                )
            if pdf.page_count > 50:
                warnings.append(
                    f"PDF has {pdf.page_count} pages — that's unusually long for an LOI."
                )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.pdf_inspect_failed run=%s err=%s", run_id, exc)

    # Byte-identical-to-draft check: if HR uploads the exact bytes we
    # generated, they almost certainly forgot to sign + scan it.
    try:
        existing_doc = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select("storage_path, file_bytes")
            .eq("run_id", run_id).eq("kind", "loi").maybe_single().execute()
        )
        if existing_doc and existing_doc.data:
            draft_path = existing_doc.data.get("storage_path")
            if draft_path:
                def _dl() -> bytes:
                    return svc.storage.from_(ob_storage.STORAGE_BUCKET).download(draft_path)
                draft_bytes = await asyncio.to_thread(_dl)
                if draft_bytes and draft_bytes == body:
                    warnings.append(
                        "This file is byte-identical to the unsigned draft — did "
                        "you forget to sign and scan it?"
                    )
    except Exception as exc:  # noqa: BLE001
        log.warning("onboarding_v2.draft_compare_failed run=%s err=%s", run_id, exc)

    storage_path = f"orgs/{org_id}/onboarding/{run_id}/loi_signed.pdf"

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=body,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "signed_pdf_path": storage_path,
                "sign_status": "signed_by_hr",
                "signed_uploaded_by": user_id,
                "signed_uploaded_at": now,
            }
        )
        .eq("run_id", run_id)
        .eq("kind", "loi")
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"status": "loi_signed_uploaded", "loi_signed_at": now})
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="loi_signed_uploaded",
        message="HR uploaded the signed LOI.",
        actor_user_id=user_id,
    )

    # Kick the agent to email the candidate.
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/loi_signed_uploaded",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "loi_signed_uploaded", "warnings": warnings}


# ── Approve AL+NDA bundle ───────────────────────────────────────────────────


@router.post("/runs/{run_id}/offer-bundle/approve")
async def approve_offer_bundle(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("id, status, candidate_email, candidate_name, role_title")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if run.data["status"] != "appointment_pending_hr_review":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{run.data['status']}', expected 'appointment_pending_hr_review'.",
        )

    docs = await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .select("id, kind, storage_path, render_context")
        .eq("run_id", run_id)
        .in_("kind", ["appointment_letter", "nda"])
        .execute()
    )
    if not docs.data or len(docs.data) < 2:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Offer bundle incomplete — both AL and NDA must exist before approval.",
        )

    from app.config import get_settings
    from app.services.email import send_email_event
    settings = get_settings()

    esign_envelope_id: str | None = None
    esign_signing_url: str | None = None

    # Prefer e-sign when apps/esign is configured; fall back to plain email
    # with signed-link PDFs. The fallback path is what a customer uses on
    # their first day before ESIGN_SERVICE_URL/ESIGN_API_KEY are wired.
    from app.services.integrations.inhouse_sign import (
        create_envelope,
        merge_pdfs,
    )
    from app.services.integrations.inhouse_sign import (
        is_configured as _esign_ready,
    )

    use_esign = _esign_ready()

    if use_esign:
        try:
            def _download(path: str) -> bytes:
                return svc.storage.from_(ob_storage.STORAGE_BUCKET).download(path)

            pdf_bytes_by_kind = {
                d["kind"]: await asyncio.to_thread(_download, d["storage_path"])
                for d in docs.data
            }
            # Deterministic order (appointment_letter, nda) so the merged
            # bundle always reads the same way regardless of query order.
            merged = merge_pdfs(
                [pdf_bytes_by_kind[k] for k in ("appointment_letter", "nda") if k in pdf_bytes_by_kind]
            )
            merged_path = f"orgs/{org_id}/onboarding/{run_id}/offer_bundle.pdf"

            def _upload() -> None:
                svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
                    path=merged_path,
                    file=merged,
                    file_options={"content-type": "application/pdf", "upsert": "true"},
                )

            await asyncio.to_thread(_upload)

            envelope = await create_envelope(
                org_id=org_id,
                run_id=run_id,
                document_kinds=["appointment_letter", "nda"],
                storage_path=merged_path,
                signers=[
                    {
                        "role": "candidate",
                        "email": run.data["candidate_email"],
                        "name": run.data["candidate_name"],
                        "routing_order": 1,
                    }
                ],
                completion_event="onboarding_v2/esign_completed",
            )
            esign_envelope_id = envelope["envelope_id"]
            esign_signing_url = envelope["signers"][0]["signing_url"]

            await asyncio.to_thread(
                lambda: svc.table("onboarding_documents")
                .update(
                    {
                        "esign_envelope_id": esign_envelope_id,
                        "esign_status": "sent",
                        "esign_signing_url": esign_signing_url,
                    }
                )
                .eq("run_id", run_id)
                .in_("kind", ["appointment_letter", "nda"])
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.esign_failed run=%s err=%s — falling back to email",
                run_id, exc,
            )
            use_esign = False

    if not use_esign:
        def _signed_url(path: str) -> str | None:
            try:
                res = svc.storage.from_(ob_storage.STORAGE_BUCKET).create_signed_url(
                    path=path, expires_in=ob_storage.SIGNED_URL_TTL_SECONDS
                )
                return res.get("signedURL") or res.get("signed_url")
            except Exception:
                return None

        appointment_url: str | None = None
        nda_url: str | None = None
        for d in docs.data:
            url = await asyncio.to_thread(lambda p=d["storage_path"]: _signed_url(p))
            if d["kind"] == "appointment_letter":
                appointment_url = url
            elif d["kind"] == "nda":
                nda_url = url

        await send_email_event(
            event_type="onboarding_offer_to_candidate",
            to=run.data["candidate_email"],
            user_id=None,
            org_id=org_id,
            dedupe_key=f"offer-{run_id}",
            data={
                "candidate_name": run.data["candidate_name"],
                "role_title": run.data["role_title"],
                "appointment_letter_url": appointment_url,
                "nda_url": nda_url,
                "app_url": settings.app_url.rstrip("/"),
            },
        )
    else:
        # Email the one-click signing link.
        await send_email_event(
            event_type="onboarding_offer_to_candidate",
            to=run.data["candidate_email"],
            user_id=None,
            org_id=org_id,
            dedupe_key=f"offer-{run_id}",
            data={
                "candidate_name": run.data["candidate_name"],
                "role_title": run.data["role_title"],
                "signing_url": esign_signing_url,
                "app_url": settings.app_url.rstrip("/"),
            },
        )

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update({"sign_status": "sent_to_candidate"})
        .eq("run_id", run_id)
        .in_("kind", ["appointment_letter", "nda"])
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update(
            {
                "status": "appointment_sent_to_candidate",
                "appointment_sent_at": now,
            }
        )
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="offer_bundle_approved",
        message="HR approved and sent the offer bundle (AL + NDA) to the candidate.",
        actor_user_id=user_id,
    )

    # Kick the agent forward (policy assignment is next).
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "appointment_sent_to_candidate"}


# ── LOIdraft review: edit in place, replace, approve ──────────────────────
#
# Two ways to change the draft, both landing as the same thing — a new
# `loi_hr_edit_r{n}.docx` + its PDF, with `hr_edited_*` stamped on the document
# row. Editing the lines in the browser is the everyday path; the .docx
# round-trip stays for edits a flat-text editor can't express (new clauses,
# tables, layout).


def _loi_docx_path(doc_row: dict[str, Any]) -> str:
    """Storage path of the LOI's current .docx.

    HR's edited copy when there is one, else the agent's render — whose
    `storage_path` column points at the PDF, with the .docx stored beside it
    under the same stem.
    """
    edited = doc_row.get("hr_edited_storage_path")
    if edited:
        return str(edited)
    pdf_path = doc_row.get("storage_path") or ""
    return pdf_path[:-4] + ".docx" if pdf_path.endswith(".pdf") else pdf_path


async def _load_loi_for_review(
    *, run_id: str, org_id: str, svc: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch the run and its LOIdocument row, asserting the run is parked in
    review. Every mutation below is only legal in that window: once an envelope
    exists, the bytes HR is looking at are the bytes someone is signing.
    """
    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select("id, status, loi_draft_revision")
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if run.data["status"] != "loi_pending_hr_review":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{run.data['status']}', expected 'loi_pending_hr_review'.",
        )

    doc = await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .select("id, storage_path, hr_edited_storage_path, hr_edit_revision")
        .eq("run_id", run_id)
        .eq("kind", "loi")
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "LOIdocument not found for this run."
        )
    return run.data, doc.data


async def _download_loi_docx(*, doc_row: dict[str, Any], svc: Any) -> bytes:
    """The LOI's current .docx bytes."""
    path = _loi_docx_path(doc_row)
    if not path.endswith(".docx"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This LOIhas no Word version stored, so it can't be edited here. "
            "Regenerate the draft and try again.",
        )
    try:
        return await asyncio.to_thread(
            lambda: svc.storage.from_(ob_storage.STORAGE_BUCKET).download(path)
        )
    except Exception as exc:  # noqa: BLE001 — storage miss, not a bug
        log.warning("onboarding_v2.loi_docx_download_failed path=%s err=%s", path, exc)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't open the LOIdraft from storage. Try again.",
        ) from exc


async def _store_loi_revision(
    *,
    org_id: str,
    run_id: str,
    user_id: str,
    docx_bytes: bytes,
    revision: int,
    event_type: str,
    message: str,
    metadata: dict[str, Any],
    svc: Any,
) -> str | None:
    """Persist a new HR revision of the LOIdraft: .docx + rendered PDF to
    storage, `hr_edited_*` onto the document row, revision onto the run, one
    audit event. Returns a signed URL for the fresh PDF.

    The PDF is rendered before anything is stamped, so a conversion failure
    leaves the previous revision intact rather than pointing the document row
    at a PDF that doesn't exist.
    """
    prefix = f"orgs/{org_id}/onboarding/{run_id}/loi_hr_edit_r{revision}"
    docx_path = f"{prefix}.docx"
    pdf_path = f"{prefix}.pdf"

    from app.services.pdf import (
        PdfRenderError,
        PdfRenderUnavailable,
        convert_docx_to_pdf,
    )

    try:
        pdf_bytes = await convert_docx_to_pdf(docx_bytes)
    except (PdfRenderError, PdfRenderUnavailable) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't convert the edited draft to PDF for preview: {exc}",
        ) from exc

    def _upload(path: str, body: bytes, mime: str) -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=path,
            file=body,
            file_options={"content-type": mime, "upsert": "true"},
        )

    await asyncio.to_thread(
        _upload,
        docx_path,
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await asyncio.to_thread(_upload, pdf_path, pdf_bytes, "application/pdf")

    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .update(
            {
                "hr_edited_storage_path": docx_path,
                "hr_edited_pdf_path": pdf_path,
                "hr_edited_by_user_id": user_id,
                "hr_edited_at": now,
                "hr_edit_revision": revision,
            }
        )
        .eq("run_id", run_id)
        .eq("kind", "loi")
        .execute()
    )
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"loi_draft_revision": revision, "loi_draft_edited_at": now})
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type=event_type,
        message=message,
        actor_user_id=user_id,
        metadata={"revision": revision, **metadata},
    )
    return await ob_storage.mint_signed_url(pdf_path)


@router.get(
    "/runs/{run_id}/loi/draft-text",
    response_model=LOIDraftTextResponse,
)
async def get_loi_draft_text(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> LOIDraftTextResponse:
    """The LOIdraft as editable lines, for the in-page editor.

    Read-only and safe to call repeatedly. Filled values are already in the
    text — HR is correcting a rendered document, not a template, so there are
    no placeholders to protect."""
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run_row, doc_row = await _load_loi_for_review(
        run_id=run_id, org_id=org_id, svc=svc
    )
    docx_bytes = await _download_loi_docx(doc_row=doc_row, svc=svc)

    try:
        paragraphs = text_edit.extract_editable_paragraphs(docx_bytes)
    except text_edit.TextEditError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    return LOIDraftTextResponse(
        revision=run_row.get("loi_draft_revision") or 0,
        fingerprint=text_edit.draft_fingerprint(docx_bytes),
        paragraphs=[
            LOIDraftParagraph(index=p.index, kind=p.kind, text=p.text)
            for p in paragraphs
        ],
    )


@router.post(
    "/runs/{run_id}/loi/edit-text",
    response_model=LOIEditTextResponse,
)
async def edit_loi_draft_text(
    run_id: str,
    body: LOIEditTextRequest,
    current_user: dict = Depends(verify_jwt),
) -> LOIEditTextResponse:
    """Write HR's edited lines back into the LOIdraft and re-render the PDF.

    Each edit's `index` locates a paragraph; its `text` overwrites that
    paragraph. Paragraphs HR didn't touch are not rewritten at all, so tables,
    headers, fonts and inline links survive.

    `fingerprint` must match the .docx the lines were read from. A mismatch
    means the draft moved underneath the editor, and the indices can no longer
    be trusted — HR is asked to reload rather than have their edits land on the
    wrong lines."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run_row, doc_row = await _load_loi_for_review(
        run_id=run_id, org_id=org_id, svc=svc
    )
    docx_bytes = await _download_loi_docx(doc_row=doc_row, svc=svc)

    if text_edit.draft_fingerprint(docx_bytes) != body.fingerprint:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This draft changed since you opened the editor. Reload the page "
            "to pick up the current version, then re-apply your changes.",
        )

    try:
        new_docx, changed = text_edit.apply_paragraph_edits(
            docx_bytes=docx_bytes,
            edits={e.index: e.text for e in body.edits},
        )
    except text_edit.TextEditError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
        ) from exc

    if changed == 0:
        # Nothing to persist — don't burn a revision number or re-render a PDF
        # for a save that changed no text.
        return LOIEditTextResponse(
            status="unchanged",
            revision=run_row.get("loi_draft_revision") or 0,
            changed_count=0,
            fingerprint=body.fingerprint,
            preview_url=None,
        )

    revision = (run_row.get("loi_draft_revision") or 0) + 1
    preview_url = await _store_loi_revision(
        org_id=org_id,
        run_id=run_id,
        user_id=user_id,
        docx_bytes=new_docx,
        revision=revision,
        event_type="loi_draft_edited",
        message=(
            f"HR edited the LOIdraft in place — {changed} line"
            f"{'' if changed == 1 else 's'} changed (revision {revision})."
        ),
        metadata={
            "source": "inline_editor",
            "changed_count": changed,
            "paragraph_indices": sorted(e.index for e in body.edits),
            "file_bytes": len(new_docx),
        },
        svc=svc,
    )

    log.info(
        "onboarding_v2.loi_edit_text org=%s run=%s revision=%d changed=%d",
        org_id, run_id, revision, changed,
    )
    return LOIEditTextResponse(
        status="ok",
        revision=revision,
        changed_count=changed,
        fingerprint=text_edit.draft_fingerprint(new_docx),
        preview_url=preview_url,
    )


@router.post(
    "/runs/{run_id}/loi/replace-draft",
    response_model=LOIReplaceDraftResponse,
)
async def replace_loi_draft(
    run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR replaces the agent-rendered LOI.docx with an edited version during
    the loi_pending_hr_review step. The uploaded .docx is stored as-is — we
    don't run variable substitution on it (HR has already seen filled values
    in the original render; their edits are final). We convert it to PDF for
    the inline preview and stamp the document row + run row.

    Only valid while the run is parked in loi_pending_hr_review. The
    signature-request step picks `hr_edited_pdf_path` over the original."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run_row, _doc_row = await _load_loi_for_review(
        run_id=run_id, org_id=org_id, svc=svc
    )

    body = await file.read()
    if not body or not body.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload must be a .docx file (PK zip header missing).",
        )
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, ".docx too large (>25MB)."
        )

    revision = (run_row.get("loi_draft_revision") or 0) + 1
    preview_url = await _store_loi_revision(
        org_id=org_id,
        run_id=run_id,
        user_id=user_id,
        docx_bytes=body,
        revision=revision,
        event_type="loi_draft_edited",
        message=f"HR uploaded an edited LOIdraft (revision {revision}).",
        metadata={"source": "docx_upload", "file_bytes": len(body)},
        svc=svc,
    )
    return {"status": "ok", "revision": revision, "preview_url": preview_url}


@router.post(
    "/runs/{run_id}/loi/approve-draft",
    response_model=LOIApproveDraftResponse,
)
async def approve_loi_draft(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR clicks 'Send for signature' on the LOIreview screen.

    Two paths depending on whether apps/esign is configured:

    * **apps/esign configured** — create a routed envelope with HR signing
      first, then the candidate. Both get a direct /sign/{token} link (HR's
      emailed immediately here; the candidate's is emailed by the
      esign/signer_turn Inngest function once HR completes). Run parks in
      `loi_pending_esign_signature` until apps/esign writes completion and
      fires `onboarding_v2/loi_signed_uploaded`.

    * **apps/esign not configured** — fall back to the legacy print/scan
      flow: transition to `loi_pending_hr_sign`, agent emails HR the PDF to
      sign offline."""
    from app.config import get_settings
    from app.services.email import send_email_event

    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    run = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .select(
            "id, status, candidate_email, candidate_name, triggered_by_user_id"
        )
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not run or not run.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    if run.data["status"] != "loi_pending_hr_review":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is in '{run.data['status']}', expected 'loi_pending_hr_review'.",
        )

    doc = await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .select("id, storage_path, hr_edited_pdf_path, render_context")
        .eq("run_id", run_id)
        .eq("kind", "loi")
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "LOIdocument row not found for this run.",
        )

    # Gate on apps/esign being configured; a half-wired setup falls back to
    # the print/scan flow instead of stranding the run mid-signature. Unlike
    # DocuSeal, no per-org template binding is needed — apps/esign signs the
    # rendered PDF directly.
    from app.services.integrations.inhouse_sign import (
        InhouseSignError,
        InhouseSignUnavailable,
        create_envelope,
    )
    from app.services.integrations.inhouse_sign import (
        is_configured as _esign_ready,
    )

    use_esign = _esign_ready()

    if use_esign:
        # Resolve HR's email from Supabase auth admin (mirrors agent.py:514).
        triggered_by = run.data.get("triggered_by_user_id") or user_id
        try:
            au = await asyncio.to_thread(
                lambda: svc.auth.admin.get_user_by_id(triggered_by)
            )
            hr_email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            hr_email = None
        if not hr_email:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Couldn't resolve the HR user's email for the signing flow. "
                "Sign out and back in, or contact support.",
            )
        # Display name comes from our users table; fall back to email prefix.
        hr_profile = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("display_name")
            .eq("id", triggered_by)
            .maybe_single()
            .execute()
        )
        hr_name = (
            (hr_profile.data or {}).get("display_name")
            or hr_email.split("@", 1)[0]
        )

        # HR's edited PDF (if any) is what gets signed — apps/esign signs
        # whatever storage_path we hand it, unlike DocuSeal's pre-built
        # template which always signed the template body regardless of edits.
        source_path = doc.data.get("hr_edited_pdf_path") or doc.data["storage_path"]

        try:
            envelope = await create_envelope(
                org_id=org_id,
                run_id=run_id,
                document_kinds=["loi"],
                storage_path=source_path,
                signers=[
                    {
                        "role": "hr",
                        "email": hr_email,
                        "name": hr_name,
                        "routing_order": 1,
                    },
                    {
                        "role": "candidate",
                        "email": run.data["candidate_email"],
                        "name": run.data["candidate_name"],
                        "routing_order": 2,
                    },
                ],
                completion_event="onboarding_v2/loi_signed_uploaded",
            )
        except (InhouseSignError, InhouseSignUnavailable) as exc:
            log.warning(
                "onboarding_v2.loi_esign_create_failed run=%s err=%s",
                run_id, exc,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Couldn't create the signing envelope: {exc}",
            ) from exc

        hr_signing_url = next(
            s["signing_url"] for s in envelope["signers"] if s["role"] == "hr"
        )
        settings = get_settings()
        try:
            await send_email_event(
                event_type="onboarding_sign_your_turn",
                to=hr_email,
                user_id=triggered_by,
                org_id=org_id,
                dedupe_key=f"sign-your-turn-{envelope['envelope_id']}-hr",
                data={
                    "recipient_name": hr_name,
                    "document_label": "LOI",
                    "signing_url": hr_signing_url,
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.loi_esign_hr_email_failed run=%s err=%s", run_id, exc)

        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update(
                {
                    "esign_envelope_id": envelope["envelope_id"],
                    "esign_status": "sent",
                    "sign_status": "sent_to_hr",
                }
            )
            .eq("run_id", run_id)
            .eq("kind", "loi")
            .execute()
        )
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update(
                {
                    "status": "loi_pending_esign_signature",
                    "loi_approved_for_signing_at": now,
                    "loi_sent_to_hr_at": now,
                }
            )
            .eq("id", run_id)
            .execute()
        )
        await ob_storage.log_onboarding_event(
            org_id=org_id,
            run_id=run_id,
            actor_kind="hr",
            event_type="loi_esign_envelope_created",
            message=(
                f"Signing envelope created for LOI. Routing: HR "
                f"({hr_email}) → candidate ({run.data['candidate_email']})."
            ),
            metadata={"envelope_id": envelope["envelope_id"]},
            actor_user_id=user_id,
        )
        return {
            "status": "loi_pending_esign_signature",
            "document_id": doc.data["id"],
        }

    # Fallback: legacy print/scan flow (apps/esign not configured).
    now = datetime.now(UTC).isoformat()
    await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update(
            {
                "status": "loi_pending_hr_sign",
                "loi_approved_for_signing_at": now,
                "loi_sent_to_hr_at": now,
            }
        )
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="loi_draft_approved",
        message=(
            "HR approved the LOIdraft and triggered the signature-request "
            "email."
        ),
        actor_user_id=user_id,
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {
        "status": "loi_pending_hr_sign",
        "document_id": doc.data["id"],
    }


@router.get("/runs/{run_id}/loi/signing-url")
async def get_loi_signing_url(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return HR's signing URL for the LOIenvelope.

    apps/esign's signing_url is a stable /sign/{token} link minted once at
    envelope-creation time — this is a pure DB read, no external API call
    needed (unlike DocuSeal's embedded-session minting)."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    env = await asyncio.to_thread(
        lambda: svc.table("onboarding_signing_envelopes")
        .select("envelope_id, signers, status")
        .eq("run_id", run_id)
        .eq("org_id", org_id)
        .contains("document_kinds", ["loi"])
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    if not env or not env.data:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No LOIenvelope found for this run.",
        )
    if env.data.get("status") in ("completed", "voided", "declined", "expired"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Envelope is {env.data['status']}; no signing URL available.",
        )

    hr_signer = next(
        (
            s for s in (env.data.get("signers") or [])
            if isinstance(s, dict) and s.get("role") == "hr"
        ),
        None,
    )
    if not hr_signer:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "HR signer not found on this envelope.",
        )
    if (hr_signer.get("status") or "").lower() == "completed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You've already signed this LOI.",
        )

    # Self-heal: if the Documenso webhook was missed, reconcile signer
    # statuses before handing HR a potentially stale signing link.
    from app.services.integrations.inhouse_sign import (
        InhouseSignError,
        reconcile_envelope,
    )

    try:
        recon = await reconcile_envelope(env.data["envelope_id"])
        if recon.get("changed"):
            # Re-read envelope: reconciliation may have advanced the state.
            env = await asyncio.to_thread(
                lambda: svc.table("onboarding_signing_envelopes")
                .select("envelope_id, signers, status")
                .eq("envelope_id", env.data["envelope_id"])
                .maybe_single()
                .execute()
            )
            hr_signer = next(
                (
                    s for s in (env.data.get("signers") or [])
                    if isinstance(s, dict) and s.get("role") == "hr"
                ),
                None,
            )
            if hr_signer and (hr_signer.get("status") or "").lower() == "completed":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "You've already signed this LOI. "
                    "The page will refresh shortly.",
                )
    except InhouseSignError:
        pass  # best-effort; fall through to return the URL

    if not hr_signer or not hr_signer.get("public_token"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This envelope predates the in-house signer and has no link — void and resend.",
        )

    from app.config import get_settings

    settings = get_settings()
    signing_url = f"{settings.app_url.rstrip('/')}/sign/{hr_signer['public_token']}"
    return {"status": "ok", "signing_url": signing_url}


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
    """Fresh signed URL to the LOI's current .docx (HR-edited if present,
    else the agent's render). Used by the Download button in the LOIreview
    panel so HR can pull the file, tweak it in Word, and re-upload."""
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    doc = await asyncio.to_thread(
        lambda: svc.table("onboarding_documents")
        .select("storage_path, hr_edited_storage_path")
        .eq("run_id", run_id)
        .eq("kind", "loi")
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LOIdocument not found.")
    signed = await ob_storage.mint_signed_url(_loi_docx_path(doc.data))
    if not signed:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't mint a download URL. Try again.",
        )
    return {"status": "ok", "docx_url": signed}
