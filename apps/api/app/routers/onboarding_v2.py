"""Onboarding v2 HR endpoints.

HR uses this surface to:
  * trigger an onboarding run (POST /onboarding/runs)
  * list & inspect runs
  * upload the signed-LOI PDF (POST /onboarding/runs/{id}/loi/upload-signed)
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
    OnboardingRunDetailRead,
    OnboardingRunRead,
    SourcesResponse,
    StartOnboardingRequest,
    TagTemplateRequest,
)
from app.services.agents.onboarding_v2 import storage as ob_storage

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

    # Fire async — the agent generates the LOI in the background.
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

    def _fetch_related() -> tuple[list, list, list]:
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
        return refs.data or [], docs.data or [], events.data or []

    refs, docs, events = await asyncio.to_thread(_fetch_related)

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
            "signed_url": d.get("signed_url"),
            "sign_status": d.get("sign_status") or "draft",
            "signed_pdf_path": d.get("signed_pdf_path"),
            "signed_uploaded_at": d.get("signed_uploaded_at"),
            "file_bytes": d.get("file_bytes"),
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


# ── Upload signed LOI ───────────────────────────────────────────────────────


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
    return {"status": "loi_signed_uploaded"}


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
        .select("id, kind, storage_path")
        .eq("run_id", run_id)
        .in_("kind", ["appointment_letter", "nda"])
        .execute()
    )
    if not docs.data or len(docs.data) < 2:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Offer bundle incomplete — both AL and NDA must exist before approval.",
        )

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

    from app.services.email import send_email_event
    from app.config import get_settings
    settings = get_settings()

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


# ── Cancel / resume ─────────────────────────────────────────────────────────


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("onboarding_runs")
        .update({"status": "cancelled"})
        .eq("id", run_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="run_cancelled",
        message="HR cancelled the onboarding.",
        actor_user_id=user_id,
    )
    return {"status": "cancelled"}


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Force the agent to inspect the run and dispatch again. Used by the
    'Retry' button on the dashboard for blocked / failed runs."""
    _, org_id, _ = _require_user(current_user)
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/resume",
            data={"onboarding_run_id": run_id, "org_id": org_id},
        )
    )
    return {"status": "ok"}


# ── Templates ───────────────────────────────────────────────────────────────


@router.get("/templates/status")
async def template_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return whether the org has each of the required KB templates tagged.
    Renders the "you're missing X" cards on the onboarding list page."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, template_kind, updated_at")
        .eq("org_id", org_id)
        .in_("template_kind", ["loi", "appointment_letter", "nda"])
        .execute()
    )
    by_kind: dict[str, dict[str, Any]] = {}
    for r in res.data or []:
        by_kind.setdefault(r["template_kind"], r)
    return {
        "loi": by_kind.get("loi"),
        "appointment_letter": by_kind.get("appointment_letter"),
        "nda": by_kind.get("nda"),
    }


@router.post("/templates")
async def tag_template(
    body: TagTemplateRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Tag a KB document as a template of a given kind. Fires the resume
    event so any blocked run picks it up."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    # Verify the document exists and belongs to this org.
    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_type")
        .eq("id", str(body.document_id))
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    # Soft warn but allow non-DOCX (admin may upload DOCX later as a version).
    if doc.data.get("file_type") and "officedocument.wordprocessing" not in (
        doc.data["file_type"] or ""
    ) and body.template_kind != "induction":
        log.warning(
            "onboarding_v2.template_non_docx doc=%s file_type=%s",
            doc.data["id"],
            doc.data.get("file_type"),
        )

    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"template_kind": body.template_kind})
        .eq("id", str(body.document_id))
        .eq("org_id", org_id)
        .execute()
    )

    # Fan-out to unblock any waiting runs.
    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/template_uploaded",
            data={"org_id": org_id, "template_kind": body.template_kind},
        )
    )
    return {"status": "ok", "template_kind": body.template_kind}
