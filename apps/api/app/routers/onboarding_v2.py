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
    HrReferencesOverrideRequest,
    ImportTemplateFromDriveRequest,
    LoiApproveDraftResponse,
    LoiReplaceDraftResponse,
    OnboardingRunDetailRead,
    OnboardingRunRead,
    SourcesResponse,
    StartOnboardingRequest,
    TagTemplateRequest,
    TemplateAnalyzeResponse,
    TemplateApplyMappingsRequest,
    TemplateApplyMappingsResponse,
    TemplateBlocksResponse,
    TemplateEditTextRequest,
    TemplateEditTextResponse,
    TemplateMappingItem,
    TemplateTextBlock,
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
        "loi_approved_for_signing_at": row.get("loi_approved_for_signing_at"),
        "loi_draft_edited_at": row.get("loi_draft_edited_at"),
        "loi_draft_revision": row.get("loi_draft_revision") or 0,
        "references_form_expires_at": row.get("references_form_expires_at"),
        "references_submitted_at": row.get("references_submitted_at"),
        "references_reminder_count": row.get("references_reminder_count") or 0,
        "references_last_reminder_at": row.get("references_last_reminder_at"),
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


# ── LOI draft review: replace + approve ────────────────────────────────────


@router.post(
    "/runs/{run_id}/loi/replace-draft",
    response_model=LoiReplaceDraftResponse,
)
async def replace_loi_draft(
    run_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR replaces the agent-rendered LOI .docx with an edited version during
    the loi_pending_hr_review step. The uploaded .docx is stored as-is — we
    don't run variable substitution on it (HR has already seen filled values
    in the original render; their edits are final). We convert it to PDF for
    the inline preview and stamp the document row + run row.

    Only valid while the run is parked in loi_pending_hr_review. The
    signature-request step picks `hr_edited_pdf_path` over the original."""
    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

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

    revision = (run.data.get("loi_draft_revision") or 0) + 1
    docx_path = (
        f"orgs/{org_id}/onboarding/{run_id}/loi_hr_edit_r{revision}.docx"
    )
    pdf_path = (
        f"orgs/{org_id}/onboarding/{run_id}/loi_hr_edit_r{revision}.pdf"
    )

    def _upload_docx() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=docx_path,
            file=body,
            file_options={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload_docx)

    # Render PDF from the edited .docx for the inline preview. Use the
    # raw converter (no Jinja substitution) — HR's edits are final.
    from app.services.pdf import (
        PdfRenderError,
        PdfRenderUnavailable,
        convert_docx_to_pdf,
    )

    try:
        pdf_bytes = await convert_docx_to_pdf(body)
    except (PdfRenderError, PdfRenderUnavailable) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't convert your edited .docx to PDF for preview: {exc}",
        ) from exc

    def _upload_pdf() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=pdf_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload_pdf)

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
        .update(
            {
                "loi_draft_revision": revision,
                "loi_draft_edited_at": now,
            }
        )
        .eq("id", run_id)
        .execute()
    )
    await ob_storage.log_onboarding_event(
        org_id=org_id,
        run_id=run_id,
        actor_kind="hr",
        event_type="loi_draft_edited",
        message=f"HR uploaded an edited LOI draft (revision {revision}).",
        actor_user_id=user_id,
        metadata={"revision": revision, "file_bytes": len(body)},
    )

    preview_url = await ob_storage.mint_signed_url(pdf_path)
    return {"status": "ok", "revision": revision, "preview_url": preview_url}


@router.post(
    "/runs/{run_id}/loi/approve-draft",
    response_model=LoiApproveDraftResponse,
)
async def approve_loi_draft(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """HR clicks 'Send for signature' on the LOI review screen.

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
            "LOI document row not found for this run.",
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
                    "document_label": "Letter of Intent",
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
            "HR approved the LOI draft and triggered the signature-request "
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
    """Return HR's signing URL for the LOI envelope.

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
            "No LOI envelope found for this run.",
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
    if not hr_signer.get("public_token"):
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
        .select("id, name, template_kind, created_at")
        .eq("org_id", org_id)
        .in_("template_kind", ["loi", "appointment_letter", "nda", "induction"])
        .execute()
    )
    by_kind: dict[str, dict[str, Any]] = {}
    for r in res.data or []:
        by_kind.setdefault(r["template_kind"], r)
    return {
        "loi": by_kind.get("loi"),
        "appointment_letter": by_kind.get("appointment_letter"),
        "nda": by_kind.get("nda"),
        "induction": by_kind.get("induction"),
    }


# Sample data used when previewing a template. Mirrors the structure of
# `OnboardingV2Agent._build_render_context` but with synthetic values so
# HR can see what their template renders like without starting a real run.
_PREVIEW_SAMPLE_CONTEXT: dict[str, Any] = {
    "candidate_name": "Asha Iyer",
    "candidate_email": "asha.iyer@example.com",
    "candidate_phone": "+91 98765 43210",
    "role_title": "Senior Product Manager",
    "designation": "Senior PM, Growth",
    "ctc": "INR 28,00,000.00",
    "ctc_amount": 2800000.0,
    "ctc_currency": "INR",
    "ctc_breakdown": {"base": 2200000, "variable": 600000},
    "start_date": "2026-08-01",
    "work_location": "Bengaluru, KA",
    "probation_period_months": 3,
    "reporting_manager_name": "Lakshmi Krishnan",
    "reporting_manager_email": "lakshmi@example.com",
    "company_name": "Acme Corp",
    "company_legal_name": "Acme Technologies Pvt Ltd",
    "company_address": "91 MG Road, Bengaluru 560001",
    "today_date": "2026-06-28",
    "jurisdiction": "India",
}


@router.post("/templates/{document_id}/preview")
async def preview_template(
    document_id: str,
    raw: bool = False,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Render the template to PDF for HR review.

    - `raw=false` (default): fill with synthetic sample candidate data so HR
      can see the final layout.
    - `raw=true`: convert the DOCX as-is — placeholders remain visible,
      useful right after uploading a template to verify which spots will
      be filled per candidate.

    Returns a freshly-minted signed URL (1h TTL) to the rendered PDF.
    Storage path is `onboarding/_previews/<doc_id>[.raw].pdf` — overwritten on
    every preview call, never associated with a real run.
    """
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_path, current_version_id, template_kind")
        .eq("id", document_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    if not doc.data.get("template_kind"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Tag this document with a template_kind before previewing.",
        )

    # Resolve the real storage path: prefer the current document_versions row,
    # fall back to the legacy documents.file_path column.
    storage_path: str | None = None
    current_version_id = doc.data.get("current_version_id")
    if current_version_id:
        version = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("file_path")
            .eq("id", current_version_id)
            .maybe_single()
            .execute()
        )
        if version and version.data:
            storage_path = version.data.get("file_path")
    if not storage_path:
        storage_path = doc.data.get("file_path")
    if not storage_path:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Template has no storage path — re-upload the DOCX.",
        )

    def _download() -> bytes:
        return svc.storage.from_(ob_storage.STORAGE_BUCKET).download(storage_path)

    try:
        docx_bytes = await asyncio.to_thread(_download)
    except Exception as exc:  # noqa: BLE001 — give HR a clear error
        log.exception(
            "template_preview.storage_download_failed doc=%s path=%s",
            document_id, storage_path,
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Couldn't download the template from storage. "
            "The upload may still be in progress — try again in a few seconds.",
        ) from exc
    if not docx_bytes or not docx_bytes.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The stored file isn't a valid DOCX. Re-upload the template.",
        )

    from app.services.pdf import (
        TemplateVariableError,
        convert_docx_to_pdf,
        render_docx_template_to_pdf,
    )

    try:
        if raw:
            pdf_bytes = await convert_docx_to_pdf(docx_bytes)
        else:
            _filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                template_bytes=docx_bytes,
                context=_PREVIEW_SAMPLE_CONTEXT,
                strict=True,
                template_kind=doc.data.get("template_kind"),
            )
    except TemplateVariableError as exc:
        # Surface the exact missing variable so HR can fix the DOCX.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "template_variable_undefined",
                "variable": exc.variable_name,
                "message": str(exc),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never 500 the preview
        log.exception(
            "template_preview.render_failed doc=%s kind=%s raw=%s",
            document_id, doc.data.get("template_kind"), raw,
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't render the preview ({type(exc).__name__}). "
            "Check the DOCX for unbalanced {{ }} or unsupported formatting.",
        ) from exc

    preview_suffix = ".raw" if raw else ""
    preview_path = (
        f"orgs/{org_id}/onboarding/_previews/{document_id}{preview_suffix}.pdf"
    )

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=preview_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )

    await asyncio.to_thread(_upload)
    signed_url = await ob_storage.mint_signed_url(preview_path)
    return {
        "status": "ok",
        "template_kind": doc.data.get("template_kind"),
        "preview_url": signed_url,
        "file_bytes": len(pdf_bytes),
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
    ):
        log.warning(
            "onboarding_v2.template_non_docx doc=%s file_type=%s",
            doc.data["id"],
            doc.data.get("file_type"),
        )

    # New templates land as `draft` so HR's analyze-edit-preview-save loop
    # doesn't expose half-finished edits to in-flight runs. Promotion to
    # `active` happens via POST /templates/{id}/save.
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update(
            {
                "template_kind": body.template_kind,
                "template_status": "draft",
            }
        )
        .eq("id", str(body.document_id))
        .eq("org_id", org_id)
        .execute()
    )
    return {"status": "ok", "template_kind": body.template_kind}


# ── Import from Google Drive ────────────────────────────────────────────────


@router.post("/templates/import-from-drive")
async def import_template_from_drive(
    body: ImportTemplateFromDriveRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Pull a Google Doc or .docx file from the org's connected Drive and
    register it as the template for `template_kind`.

    Three steps: (1) download/export DOCX bytes from Drive, (2) upload them
    to Supabase Storage at the same per-org prefix the direct-upload flow
    uses, (3) insert a `documents` row tagged with `template_kind` and mark
    status=ready (we skip chunk/embed — templates are rendered by the agent,
    never searched). Finally we fan-out `template_uploaded` so any run parked
    in `blocked_missing_template` resumes.
    """
    import uuid as _uuid

    from app.services.integrations import drive as drive_svc

    user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    # Refuse unsupported mimes BEFORE we touch Drive — saves a round-trip and
    # gives HR a clean error if they somehow get a PDF id through the picker
    # (defence in depth — the picker also restricts mime types).
    if body.mime_type not in (drive_svc.GOOGLE_DOC_MIME, drive_svc.DOCX_MIME):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Templates must be Google Docs or .docx files.",
        )

    # 1. Pull bytes from Drive (export Google Doc → DOCX, or raw .docx download).
    try:
        docx_bytes = await drive_svc.download_template_as_docx(
            org_id=org_id,
            file_id=body.file_id,
            mime_type=body.mime_type,
        )
    except PermissionError as exc:
        # drive_not_connected or drive_auth_failed
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Google Drive is not connected or authorisation failed ({exc}). "
            "Reconnect Drive from Settings → Integrations.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "onboarding_v2.drive_template_download_failed file_id=%s", body.file_id
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't download that file from Google Drive — please retry.",
        ) from exc

    if not docx_bytes or len(docx_bytes) < 200:
        # docxtpl needs a real .docx (a zip) — refuse empty / truncated bodies.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The downloaded file looks empty or corrupted.",
        )
    if len(docx_bytes) > 25 * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Template is too large (>25MB). Trim and retry.",
        )

    # 2. Sanity-check this is actually a DOCX. Real .docx files start with
    # `PK\x03\x04` (zip magic). A wrong-mime / wrong-export catches here
    # before it confuses docxtpl later. Cheap and fail-fast.
    if not docx_bytes.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Imported file is not a valid DOCX. Re-export from Google Drive.",
        )

    # 3. Upload to Storage. We mirror the direct-upload path:
    #    orgs/{org_id}/docs/{doc_id}/{safe_name}.docx
    # so it's indistinguishable from a browser-uploaded template at every
    # downstream layer (storage GC, signed URLs, the agent fetch helper).
    doc_id = str(_uuid.uuid4())
    base_name = (body.file_name or "Template").strip().replace("/", "_").replace("..", "_")
    if not base_name.lower().endswith(".docx"):
        base_name = f"{base_name}.docx"
    storage_path = f"orgs/{org_id}/docs/{doc_id}/{base_name}"

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=docx_bytes,
            file_options={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "upsert": "true",
            },
        )

    try:
        await asyncio.to_thread(_upload)
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "onboarding_v2.drive_template_storage_upload_failed doc=%s", doc_id
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Failed to save the imported template to storage.",
        ) from exc

    # 4. Insert the documents row tagged as a template. We mark status=ready
    # (skipping the chunk/embed pipeline) because templates are rendered by
    # the onboarding agent — they never participate in chat search. Marked
    # template_status='draft' so the agent doesn't pick it up until HR has
    # reviewed the preview and clicked Save.
    def _insert_doc() -> None:
        svc.table("documents").insert(
            {
                "id": doc_id,
                "org_id": org_id,
                "name": base_name,
                "file_path": storage_path,
                "file_type": "docx",
                "file_size_bytes": len(docx_bytes),
                "status": "ready",
                "created_by": user_id,
                "template_kind": body.template_kind,
                "template_status": "draft",
                "source": "google_drive",
                "external_id": body.file_id,
            }
        ).execute()

    try:
        await asyncio.to_thread(_insert_doc)
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "onboarding_v2.drive_template_doc_insert_failed doc=%s", doc_id
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Failed to register the imported template.",
        ) from exc

    # Note: we deliberately don't fire template_uploaded here — the template
    # is in draft state until HR reviews + saves it. The save endpoint emits
    # the fan-out event so blocked runs only pick up a finalised template.
    return {
        "status": "ok",
        "document_id": doc_id,
        "template_kind": body.template_kind,
        "name": base_name,
        "file_bytes": len(docx_bytes),
    }


# ── AI-assisted template conversion ─────────────────────────────────────────


_TEMPLATE_TEXT_PREVIEW_CHARS = 1500


async def _load_template_docx(
    *, document_id: str, org_id: str, svc: Any
) -> tuple[bytes, dict[str, Any], str]:
    """Resolve a template document's current storage path and download it.

    Returns (docx_bytes, document_row, storage_path). Raises HTTPException on
    common error states (not found, untagged, missing storage path) so each
    caller doesn't repeat the same plumbing.
    """
    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_path, current_version_id, template_kind, file_type")
        .eq("id", document_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    if not doc.data.get("template_kind"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Tag this document with a template_kind first.",
        )

    storage_path: str | None = None
    current_version_id = doc.data.get("current_version_id")
    if current_version_id:
        version = await asyncio.to_thread(
            lambda: svc.table("document_versions")
            .select("file_path")
            .eq("id", current_version_id)
            .maybe_single()
            .execute()
        )
        if version and version.data:
            storage_path = version.data.get("file_path")
    if not storage_path:
        storage_path = doc.data.get("file_path")
    if not storage_path:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Template has no storage path — re-upload the DOCX.",
        )

    def _download() -> bytes:
        return svc.storage.from_(ob_storage.STORAGE_BUCKET).download(storage_path)

    try:
        docx_bytes = await asyncio.to_thread(_download)
    except Exception as exc:  # noqa: BLE001 — surface a meaningful error
        log.exception(
            "template_load.storage_download_failed doc=%s path=%s",
            document_id, storage_path,
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Couldn't download the template from storage. "
            "The upload may still be in progress — try again in a few seconds.",
        ) from exc
    if not docx_bytes or not docx_bytes.startswith(b"PK"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The stored file isn't a valid DOCX. Re-upload the template.",
        )
    return docx_bytes, doc.data, storage_path


@router.post("/templates/{document_id}/analyze", response_model=TemplateAnalyzeResponse)
async def analyze_template(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> TemplateAnalyzeResponse:
    """Detect whether a tagged template has Jinja `{{ }}` placeholders. If
    not, call the LLM to propose blank → variable mappings that HR can then
    confirm via POST /templates/{id}/apply-mappings.

    This is the entry point of the AI-assisted conversion flow. Safe to call
    multiple times — the LLM call is the only meaningful cost and the result
    is not persisted on this endpoint."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    from app.services.agents.onboarding_v2.template_analyzer import (
        TemplateAnalyzerError,
        extract_text,
        has_jinja_placeholders,
        propose_mappings,
    )
    from app.services.agents.onboarding_v2.template_vars import TEMPLATE_VARIABLES

    docx_bytes, doc_row, _ = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )

    template_kind = doc_row.get("template_kind") or "loi"

    try:
        already_has_placeholders = has_jinja_placeholders(docx_bytes)
        text = extract_text(docx_bytes)
    except Exception as exc:  # noqa: BLE001 — surface as 422 to HR
        log.warning("template_analyze.extract_failed doc=%s err=%s", document_id, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't read this DOCX. It may be corrupted or password-protected. ({exc})",
        ) from exc

    available_variables = [
        {"name": v["name"], "label": v["label"], "description": v["description"]}
        for v in TEMPLATE_VARIABLES
    ]

    # Always run the LLM analysis — a template can have BOTH `{{ }}` placeholders
    # (already templated) AND blank spots (still need mapping). The prompt
    # instructs the model to skip Jinja regions and only propose mappings for
    # remaining blanks. `has_placeholders` becomes informational, not a
    # short-circuit. When `mappings == []` and `has_placeholders == True`,
    # the UI shows "your template is fully templated".
    warning: str | None = None
    try:
        proposed = await propose_mappings(docx_text=text, template_kind=template_kind)
    except TemplateAnalyzerError as exc:
        log.warning("template_analyze.llm_failed doc=%s err=%s", document_id, exc)
        proposed = []
        warning = str(exc)
    except Exception as exc:  # noqa: BLE001 — never 500 here, soft-fail with a warning
        log.exception("template_analyze.llm_unexpected doc=%s", document_id)
        proposed = []
        warning = (
            "AI analyzer hit an unexpected error and was skipped. "
            "You can re-upload or add {{ variable }} placeholders manually. "
            f"({type(exc).__name__})"
        )

    # Clamp fields so an over-long LLM output can't fail Pydantic validation
    # (TemplateMappingItem caps blank_text at 500 and context_* at 200).
    mapping_items: list[TemplateMappingItem] = []
    for m in proposed:
        try:
            mapping_items.append(
                TemplateMappingItem(
                    blank_text=(m.blank_text or "")[:500],
                    variable=m.variable,
                    context_before=(m.context_before or "")[:200],
                    context_after=(m.context_after or "")[:200],
                    confidence=m.confidence if m.confidence in ("high", "medium", "low") else "medium",
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "template_analyze.mapping_drop doc=%s blank=%r err=%s",
                document_id, (m.blank_text or "")[:80], exc,
            )

    return TemplateAnalyzeResponse(
        document_id=document_id,
        template_kind=template_kind,
        has_placeholders=already_has_placeholders,
        mappings=mapping_items,
        text_preview=text[:_TEMPLATE_TEXT_PREVIEW_CHARS],
        available_variables=available_variables,
        warning=warning,
    )


@router.post(
    "/templates/{document_id}/apply-mappings",
    response_model=TemplateApplyMappingsResponse,
)
async def apply_template_mappings(
    document_id: str,
    body: TemplateApplyMappingsRequest,
    current_user: dict = Depends(verify_jwt),
) -> TemplateApplyMappingsResponse:
    """Take HR-confirmed mappings, rewrite the DOCX with Jinja placeholders,
    validate the rewrite by dry-rendering with sample data, then overwrite
    the template in storage.

    Idempotent in the sense that re-applying the same mappings produces the
    same DOCX bytes — but every call DOES re-upload, so callers should only
    call this once HR has approved. Logs an onboarding event for audit."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    from app.services.agents.onboarding_v2.template_analyzer import (
        ProposedMapping,
        TemplateAnalyzerError,
        apply_mappings,
        validate_rendered,
    )
    from app.services.agents.onboarding_v2.template_vars import get_variable_names

    docx_bytes, doc_row, storage_path = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )
    template_kind = doc_row.get("template_kind") or "loi"

    allowed = set(get_variable_names())
    for m in body.mappings:
        if m.variable not in allowed:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown variable '{m.variable}'. Allowed: {sorted(allowed)}",
            )

    mappings = [
        ProposedMapping(
            blank_text=m.blank_text,
            variable=m.variable,
            context_before=m.context_before,
            context_after=m.context_after,
            confidence=m.confidence,
        )
        for m in body.mappings
    ]

    try:
        new_docx = apply_mappings(docx_bytes=docx_bytes, mappings=mappings)
    except TemplateAnalyzerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Dry-render with sample data so a broken template doesn't reach a run.
    try:
        await validate_rendered(new_docx, template_kind=template_kind)
    except TemplateAnalyzerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # Overwrite the storage object. We deliberately replace in-place rather
    # than creating a new document_versions row — the original DOCX is the
    # legal source-of-truth for HR; the converted version is a derivative
    # used by the agent. HR can re-upload to revert.
    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=new_docx,
            file_options={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload)

    # Best-effort preview render so the UI can immediately show HR the
    # converted template's output. A preview failure shouldn't fail the
    # apply call — the template is already saved.
    preview_url: str | None = None
    try:
        from app.services.agents.onboarding_v2.template_vars import (
            PREVIEW_SAMPLE_CONTEXT,
        )
        from app.services.pdf import render_docx_template_to_pdf

        _, pdf_bytes = await render_docx_template_to_pdf(
            template_bytes=new_docx,
            context=PREVIEW_SAMPLE_CONTEXT,
            strict=True,
            template_kind=template_kind,
        )
        preview_path = f"orgs/{org_id}/onboarding/_previews/{document_id}.pdf"

        def _preview_upload() -> None:
            svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
                path=preview_path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf", "upsert": "true"},
            )

        await asyncio.to_thread(_preview_upload)
        preview_url = await ob_storage.mint_signed_url(preview_path)
    except Exception as exc:  # noqa: BLE001 — preview is best-effort
        log.info("template_apply.preview_render_skipped doc=%s err=%s", document_id, exc)

    log.info(
        "template_apply.success org=%s doc=%s kind=%s mappings=%d",
        org_id, document_id, template_kind, len(body.mappings),
    )

    # Applying mappings is part of the iterate/preview loop, not the final
    # save. Reset template_status to 'draft' so the agent doesn't see a
    # half-built template, and let POST /templates/{id}/save fire the
    # fan-out event when HR is happy.
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"template_status": "draft"})
        .eq("id", document_id)
        .eq("org_id", org_id)
        .execute()
    )

    return TemplateApplyMappingsResponse(
        document_id=document_id,
        template_kind=template_kind,
        applied_count=len(body.mappings),
        preview_url=preview_url,
    )


# ── Flat-text review: edit the template as text, preview on demand ──────────


@router.get(
    "/templates/{document_id}/blocks",
    response_model=TemplateBlocksResponse,
)
async def get_template_blocks(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> TemplateBlocksResponse:
    """Return the current DOCX as an ordered list of editable text blocks.

    HR reviews and edits these lines directly (placeholders stay visible),
    then persists via POST /templates/{id}/edit-text. Read-only — safe to call
    repeatedly."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    from app.services.agents.onboarding_v2.template_analyzer import (
        extract_editable_blocks,
    )

    docx_bytes, doc_row, _ = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )
    template_kind = doc_row.get("template_kind") or "loi"

    try:
        blocks = extract_editable_blocks(docx_bytes)
    except Exception as exc:  # noqa: BLE001 — surface as 422 to HR
        log.warning("template_blocks.extract_failed doc=%s err=%s", document_id, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't read this DOCX. It may be corrupted or password-protected. ({exc})",
        ) from exc

    return TemplateBlocksResponse(
        document_id=document_id,
        template_kind=template_kind,
        blocks=[
            TemplateTextBlock(index=b.index, text=b.text, kind=b.kind) for b in blocks
        ],
    )


@router.post(
    "/templates/{document_id}/edit-text",
    response_model=TemplateEditTextResponse,
)
async def edit_template_text(
    document_id: str,
    body: TemplateEditTextRequest,
    current_user: dict = Depends(verify_jwt),
) -> TemplateEditTextResponse:
    """Write HR's edited text blocks back into the template DOCX in place.

    Each block's `index` locates the paragraph; its `text` overwrites that
    paragraph's runs, preserving fonts/tables/headers and existing
    `{{ placeholders }}`. Before persisting we dry-render with sample data so a
    broken edit (unbalanced braces, unknown variable) is rejected with a clear
    message rather than silently poisoning the template. Forces the template
    back to `draft` — HR still has to Save to promote it. Returns a fresh raw
    preview URL (placeholders visible)."""
    _, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    from app.services.agents.onboarding_v2.template_analyzer import (
        TemplateAnalyzerError,
        apply_text_edits,
        validate_rendered,
    )

    docx_bytes, doc_row, storage_path = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )
    template_kind = doc_row.get("template_kind") or "loi"

    edits = {b.index: b.text for b in body.edits}
    try:
        new_docx, changed = apply_text_edits(docx_bytes=docx_bytes, edits=edits)
    except Exception as exc:  # noqa: BLE001 — surface as 422 to HR
        log.warning("template_edit_text.apply_failed doc=%s err=%s", document_id, exc)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Couldn't apply your edits to the document. ({exc})",
        ) from exc

    # Guard: a broken edit (unbalanced {{ }}, unknown variable) must not reach
    # a run. Dry-render with sample data; on failure, reject WITHOUT persisting
    # so the good version stays intact and HR sees exactly what to fix.
    try:
        await validate_rendered(new_docx, template_kind=template_kind)
    except TemplateAnalyzerError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=new_docx,
            file_options={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload)

    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"template_status": "draft", "file_size_bytes": len(new_docx)})
        .eq("id", document_id)
        .eq("org_id", org_id)
        .execute()
    )

    # Raw preview — DOCX as-is, placeholders visible, no sample data. Matches
    # the "show the template as-is" review preview. Best-effort: validation
    # already passed the strict render, so this should essentially always work.
    preview_url: str | None = None
    preview_error: str | None = None
    try:
        from app.services.pdf import convert_docx_to_pdf

        pdf_bytes = await convert_docx_to_pdf(new_docx)
        preview_path = f"orgs/{org_id}/onboarding/_previews/{document_id}.raw.pdf"

        def _preview_upload() -> None:
            svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
                path=preview_path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf", "upsert": "true"},
            )

        await asyncio.to_thread(_preview_upload)
        preview_url = await ob_storage.mint_signed_url(preview_path)
    except Exception as exc:  # noqa: BLE001 — preview is best-effort
        preview_error = (
            f"Saved your edits, but couldn't render a preview ({type(exc).__name__})."
        )
        log.info("template_edit_text.preview_failed doc=%s err=%s", document_id, exc)

    log.info(
        "template_edit_text.success org=%s doc=%s kind=%s changed=%d",
        org_id, document_id, template_kind, changed,
    )

    return TemplateEditTextResponse(
        document_id=document_id,
        template_kind=template_kind,
        changed_count=changed,
        preview_url=preview_url,
        preview_error=preview_error,
    )


# ── Template draft/save: edit loop + final promotion ───────────────────────


@router.post("/templates/{document_id}/replace")
async def replace_template_docx(
    document_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Overwrite a template's .docx bytes with an HR-edited version.

    Used after HR downloads the .docx, tweaks it in Word, and re-uploads.
    The template is forced back into `draft` status so the agent doesn't
    serve a half-edited version — HR has to click Save (POST .../save) to
    promote it again. Returns a fresh preview signed URL."""
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

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

    _docx_bytes, doc_row, storage_path = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )

    def _upload() -> None:
        svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
            path=storage_path,
            file=body,
            file_options={
                "content-type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "upsert": "true",
            },
        )

    await asyncio.to_thread(_upload)

    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update(
            {
                "template_status": "draft",
                "file_size_bytes": len(body),
            }
        )
        .eq("id", document_id)
        .eq("org_id", org_id)
        .execute()
    )

    # Render a fresh preview from the new bytes — best-effort. If it fails
    # (e.g. HR's edit broke variable braces) we still return ok; the UI shows
    # an analyze-and-fix prompt for that case.
    template_kind = doc_row.get("template_kind") or "loi"
    preview_url: str | None = None
    preview_error: str | None = None
    try:
        from app.services.agents.onboarding_v2.template_vars import (
            PREVIEW_SAMPLE_CONTEXT,
        )
        from app.services.pdf import render_docx_template_to_pdf

        _, pdf_bytes = await render_docx_template_to_pdf(
            template_bytes=body,
            context=PREVIEW_SAMPLE_CONTEXT,
            strict=True,
            template_kind=template_kind,
        )
        preview_path = f"orgs/{org_id}/onboarding/_previews/{document_id}.pdf"

        def _preview_upload() -> None:
            svc.storage.from_(ob_storage.STORAGE_BUCKET).upload(
                path=preview_path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf", "upsert": "true"},
            )

        await asyncio.to_thread(_preview_upload)
        preview_url = await ob_storage.mint_signed_url(preview_path)
    except Exception as exc:  # noqa: BLE001 — preview is best-effort
        preview_error = (
            f"Couldn't render a preview from your edited .docx ({type(exc).__name__}). "
            "Run Analyze to check the placeholders."
        )
        log.info(
            "template_replace.preview_render_failed doc=%s err=%s",
            document_id, exc,
        )

    return {
        "status": "ok",
        "template_kind": template_kind,
        "preview_url": preview_url,
        "preview_error": preview_error,
        "file_bytes": len(body),
    }


@router.post("/templates/{document_id}/save")
async def save_template(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Promote a draft template to `active`. After this:
      * the OnboardingV2Agent will pick it up (fetch_template_docx filters
        by template_status='active');
      * a fan-out `template_uploaded` event resumes any run parked on
        `blocked_missing_template` for this kind.
    """
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, template_kind, template_status")
        .eq("id", document_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    if not doc.data.get("template_kind"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Tag this document with a template_kind first.",
        )

    template_kind = doc.data["template_kind"]
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"template_status": "active"})
        .eq("id", document_id)
        .eq("org_id", org_id)
        .execute()
    )

    # Mark any older active template of the same kind as `archived` — we
    # keep only one active template per kind per org so the agent's
    # most-recent-active lookup is deterministic.
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update({"template_status": "archived"})
        .eq("org_id", org_id)
        .eq("template_kind", template_kind)
        .eq("template_status", "active")
        .neq("id", document_id)
        .execute()
    )

    inngest_client = get_inngest_client()
    await inngest_client.send(
        inngest.Event(
            name="onboarding_v2/template_uploaded",
            data={"org_id": org_id, "template_kind": template_kind},
        )
    )
    return {"status": "ok", "template_kind": template_kind, "saved": True}


@router.get("/templates/{document_id}/docx-url")
async def get_template_docx_url(
    document_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Return a fresh short-lived signed URL pointing to the template's
    current .docx bytes. The UI uses this to expose a Download button so HR
    can grab the file, edit it in Word, and re-upload via /replace."""
    _user_id, org_id, _ = _require_user(current_user)
    svc = get_service_client()

    _docx_bytes, doc_row, storage_path = await _load_template_docx(
        document_id=document_id, org_id=org_id, svc=svc
    )
    signed = await ob_storage.mint_signed_url(storage_path)
    if not signed:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't mint a download URL. Try again.",
        )
    return {
        "status": "ok",
        "docx_url": signed,
        "template_kind": doc_row.get("template_kind"),
        "name": doc_row.get("name"),
    }


@router.get("/runs/{run_id}/loi/docx-url")
async def get_loi_docx_url(
    run_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Fresh signed URL to the LOI's current .docx (HR-edited if present,
    else the agent's render). Used by the Download button in the LOI review
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LOI document not found.")
    # The agent stores filled .docx alongside the PDF — but the storage_path
    # column points at the PDF in our pipeline. Derive the .docx path by
    # swapping the extension.
    docx_path = doc.data.get("hr_edited_storage_path")
    if not docx_path:
        pdf_path = doc.data.get("storage_path") or ""
        docx_path = pdf_path[:-4] + ".docx" if pdf_path.endswith(".pdf") else pdf_path
    signed = await ob_storage.mint_signed_url(docx_path)
    if not signed:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't mint a download URL. Try again.",
        )
    return {"status": "ok", "docx_url": signed}
