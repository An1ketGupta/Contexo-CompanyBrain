"""OnboardingV2Agent — autonomous pre-join HR pipeline.

Triggered when HR clicks "Mark Hired & Start Onboarding" on a recruiting
candidate. The run is checkpointed in `onboarding_runs.status` so it can
park between human steps without losing context:

  draft
    │
    ▼
  loi_generating ─── BLOCKED if no LOI template in KB
    │
    ▼
  loi_pending_hr_review  ── HR previews the filled LOI; may download .docx,
    │                       edit in Word, re-upload. Repeats until HR clicks
    │                       "Send for signature". Agent parks until then.
    │
    ├─(apps/esign configured)─▶ loi_pending_esign_signature ── HR → candidate
    │                             signing envelope routed via apps/esign. Both
    │                             sign in-app; apps/esign stamps the LOI doc and
    │                             fires onboarding_v2/loi_signed_uploaded (the
    │                             run status stays loi_pending_esign_signature —
    │                             we advance off the doc's esign_status). ──┐
    ▼                                                                       │
  loi_pending_hr_sign  ── (fallback: apps/esign not configured) HR is        │
    │                       emailed the LOI; downloads, prints, signs, scans, │
    │                       uploads the signed PDF                            │
    ▼                                                                        │
  loi_signed_uploaded                                                        │
    │◀───────────────────────────────────────────────────────────────────────┘
    ▼
  loi_sent_to_candidate ── Candidate email contains the signed LOI + a
    │                       token-gated public link to submit BGV refs.
    ▼
  awaiting_candidate_references ── Candidate fills the references form.
    │                                Cron sends reminders every 3 days. HR has
    │                                an override button to enter refs manually.
    ▼
  bgv_pending  ── References each get a tokenised URL; wait for responses
    │             (Inngest cron sends reminders after 3 days)
    ▼
  bgv_complete
    │
    ▼
  appointment_bundle_generating ── BLOCKED if no AL or NDA template
    │
    ▼
  appointment_pending_hr_review ── HR one-click approve
    │
    ▼
  appointment_sent_to_candidate
    │
    ▼
  policies_assigned  ── reuses the compliance ack table from migration 031
    │
    ▼
  policies_acknowledged
    │
    ▼
  induction_generating  ── BLOCKED if no induction template in KB
    │
    ▼
  induction_sent
    │
    ▼
  completed

The agent's `run()` method is re-entrant — it inspects the current status
and dispatches to the right step. Inngest functions kick it with the
current status hint so it can resume cleanly from any checkpoint.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.agents.onboarding_v2.pre_join import (
    ensure_pre_join_user,
    send_magic_link,
)
from app.services.agents.onboarding_v2.template_vars import SIGNATURE_BLOCK_MARKERS
from app.services.email import send_email_event
from app.services.pdf import (
    PdfRenderError,
    PdfRenderUnavailable,
    TemplateVariableError,
    render_docx_template_to_pdf,
)

log = get_logger(__name__)


# Mapping from `kind` to the documents.template_kind value we look up.
# Kept here (not in storage.py) because it's policy, not plumbing.
_TEMPLATE_KIND_FOR_KIND = {
    "loi": "loi",
    "appointment_letter": "appointment_letter",
    "nda": "nda",
}


class OnboardingV2Agent(BaseAgent):
    agent_type = "onboarding_v2"

    def __init__(
        self,
        *,
        org_id: str,
        run_id: str,
        triggered_by_user_id: str | None = None,
        resume_from: str | None = None,
    ) -> None:
        # We pass run_id explicitly so the agent_runs row id matches the
        # onboarding_runs.agent_run_id pointer set at row creation time.
        super().__init__(
            org_id=org_id,
            input_data={"onboarding_run_id": run_id, "resume_from": resume_from},
            triggered_by="webhook",
            triggered_by_user_id=triggered_by_user_id,
        )
        self.onboarding_run_id = run_id
        self.resume_from = resume_from
        self._run_row: dict[str, Any] | None = None
        # Bound logger — every emit carries run/org context without per-call boilerplate.
        self.log = log.bind(
            onboarding_run_id=run_id,
            org_id=org_id,
            agent="onboarding_v2",
        )

    # ── State helpers ──────────────────────────────────────────────────────

    async def _load_run(self) -> dict[str, Any]:
        if self._run_row is not None:
            return self._run_row
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .select("*")
            .eq("id", self.onboarding_run_id)
            .maybe_single()
            .execute()
        )
        if not res or not res.data:
            raise RuntimeError(f"onboarding_run_not_found:{self.onboarding_run_id}")
        self._run_row = res.data
        return res.data

    async def _refresh_run(self) -> dict[str, Any]:
        self._run_row = None
        return await self._load_run()

    async def _set_status(
        self,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        svc = get_service_client()
        # Always clear blocked_reason on status transitions unless the caller
        # explicitly sets it (error / block states).
        payload: dict[str, Any] = {"status": status, "current_step": status, "blocked_reason": None}
        if extra:
            payload.update(extra)
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update(payload)
            .eq("id", self.onboarding_run_id)
            .execute()
        )
        self._run_row = None

    async def _block_missing_template(self, template_kind: str) -> dict[str, Any]:
        """Park the run in blocked_missing_template; HR will see a clear
        prompt to upload the template and the agent will be re-kicked when
        the upload happens."""
        await self._set_status(
            "blocked_missing_template",
            extra={
                "blocked_reason": (
                    f"No {template_kind.replace('_', ' ')} template uploaded. "
                    "Configure it in Onboarding → Document templates."
                ),
                "blocked_template_kind": template_kind,
            },
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="blocked_missing_template",
            message=(
                f"No {template_kind.replace('_', ' ')} template configured. "
                "Go to Onboarding → Document templates and upload a DOCX for this slot."
            ),
            metadata={"template_kind": template_kind},
        )
        return {"status": "blocked_missing_template", "missing": template_kind}

    async def _resolve_org_name(self) -> str:
        svc = get_service_client()
        row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("name")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        return (row.data or {}).get("name") or "your company"

    async def _resolve_org_branding(self) -> dict[str, Any]:
        """Pull org name, legal name, jurisdiction, logo for templates +
        induction PDF. Returns sane defaults if a column isn't populated."""
        svc = get_service_client()
        row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("name, legal_name, jurisdiction, logo_url, registered_address")
            .eq("id", self.org_id)
            .maybe_single()
            .execute()
        )
        data = (row.data if row else {}) or {}
        return {
            "name": data.get("name") or "your company",
            "legal_name": data.get("legal_name") or data.get("name") or "your company",
            "jurisdiction": data.get("jurisdiction") or "India",
            "logo_url": data.get("logo_url"),
            "registered_address": data.get("registered_address") or "",
        }

    # ── Render context ────────────────────────────────────────────────────

    async def _build_render_context(self) -> dict[str, Any]:
        """The dict handed to docxtpl for placeholder substitution. Tracks
        the variables a customer template can reference. Documented in
        apps/api/tools/ONBOARDING_TEMPLATE_VARS.md."""
        run = await self._load_run()
        branding = await self._resolve_org_branding()
        ctc_amount = run.get("ctc_amount")
        ctc_currency = run.get("ctc_currency") or "INR"
        ctc_formatted = (
            f"{ctc_currency} {float(ctc_amount):,.2f}" if ctc_amount else "TBD"
        )
        today = datetime.now(UTC).date().isoformat()
        return {
            "candidate_name": run["candidate_name"],
            "candidate_email": run["candidate_email"],
            "candidate_phone": run.get("candidate_phone") or "",
            "role_title": run["role_title"],
            "designation": run.get("designation") or run["role_title"],
            "ctc": ctc_formatted,
            "ctc_amount": float(ctc_amount) if ctc_amount else 0.0,
            "ctc_currency": ctc_currency,
            "ctc_breakdown": run.get("ctc_breakdown") or {},
            "start_date": str(run["start_date"]),
            "work_location": run.get("work_location") or "",
            "probation_period_months": run.get("probation_period_months") or 0,
            "reporting_manager_name": run.get("reporting_manager_name") or "",
            "reporting_manager_email": run.get("reporting_manager_email") or "",
            "company_name": branding["name"],
            "company_legal_name": branding["legal_name"],
            "company_address": branding["registered_address"],
            "today_date": today,
            "jurisdiction": branding["jurisdiction"],
            # Sentinel markers apps/esign's pdf_sign.py searches for to place
            # each signer's signature — see SIGNATURE_BLOCK_MARKERS.
            **SIGNATURE_BLOCK_MARKERS,
        }

    # ── Main dispatcher ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Re-entrant: inspect the run's current status and dispatch.

        Each step transitions status forward. Steps that wait for human input
        (loi_pending_hr_sign, bgv_pending, appointment_pending_hr_review,
        policies_assigned) exit the agent without erroring — the run is
        re-kicked when the human acts.
        """
        run = await self._load_run()
        current = run.get("status") or "draft"
        await self.log_step(
            "dispatch",
            "started",
            {"current_status": current, "resume_from": self.resume_from},
        )

        # Terminal states: never re-drive. A late Inngest event (e.g. a BGV
        # response that came in after HR cancelled) must not resurrect the
        # run or trigger any external side effect.
        if current in ("cancelled", "completed", "failed"):
            return {"status": current, "terminal": True}

        if current in ("draft", "loi_generating"):
            return await self._step_generate_loi()
        if current == "loi_pending_hr_review":
            # HR is reviewing/editing the LOI draft. The run is re-kicked
            # from the approve-draft route which transitions us to
            # loi_pending_hr_sign and dispatches into the signature step.
            return {"status": current, "waiting_for": "hr_to_approve_loi_draft"}
        if current == "loi_pending_hr_sign":
            return await self._step_send_to_hr_for_signature()
        if current == "loi_pending_esign_signature":
            # Parked while the HR → candidate signing envelope is out. apps/esign
            # fires onboarding_v2/loi_signed_uploaded once BOTH signers complete,
            # re-kicking us here. It only updates onboarding_documents (not the
            # run status), so advance off the doc's esign state. Guard against a
            # stray kick mid-signing emailing the candidate before both signed.
            if await self._loi_esign_completed():
                return await self._step_send_loi_to_candidate()
            return {"status": current, "waiting_for": "loi_esign_signatures"}
        if current == "loi_signed_uploaded":
            return await self._step_send_loi_to_candidate()
        if current == "loi_sent_to_candidate":
            # Candidate has the LOI; references-form link is in their email.
            # Park until the candidate submits (or HR overrides).
            return await self._step_wait_for_candidate_references()
        if current == "awaiting_candidate_references":
            return await self._step_wait_for_candidate_references()
        if current == "bgv_pending":
            return await self._step_check_bgv_completion()
        if current == "bgv_complete":
            return await self._step_generate_offer_bundle()
        if current == "appointment_pending_hr_review":
            return {"status": current, "waiting_for": "hr_to_approve_bundle"}
        if current == "appointment_sent_to_candidate":
            return await self._step_assign_policies()
        if current == "policies_assigned":
            return await self._step_check_policy_acks()
        if current == "policies_acknowledged":
            return await self._step_generate_induction()
        if current == "induction_sent":
            return await self._step_finalise()
        if current == "blocked_missing_template":
            # Re-kicked after HR uploads a template — re-attempt the LOI/AL/NDA
            # step that triggered the block.
            missing = (run.get("blocked_template_kind") or "loi").lower()
            if missing == "loi":
                return await self._step_generate_loi()
            if missing in ("appointment_letter", "nda"):
                return await self._step_generate_offer_bundle()
            if missing == "induction":
                return await self._step_generate_induction()

        return {"status": current, "no_op": True}

    # ── Step 1: LOI ────────────────────────────────────────────────────────

    async def _step_generate_loi(self) -> dict[str, Any]:
        await self.log_step("generate_loi", "started")

        # Check for the KB-tagged LOI template BEFORE flipping status to
        # loi_generating — otherwise a missing template (or a crash between
        # the two writes) leaves the run stuck on a misleading
        # "Generating LOI" label.
        fetched = await ob_storage.fetch_template_docx(
            org_id=self.org_id, template_kind="loi"
        )
        if not fetched:
            await self.log_step("generate_loi", "skipped", {"reason": "no_template"})
            return await self._block_missing_template("loi")

        await self._set_status("loi_generating")
        docx_bytes, doc_row = fetched
        ctx = await self._build_render_context()

        try:
            filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                template_bytes=docx_bytes,
                context=ctx,
                strict=True,
                template_kind="loi",
            )
        except PdfRenderUnavailable as exc:
            # Deployment config issue (Gotenberg not running). Reset to draft
            # so the run is retryable once the sidecar is provisioned.
            await self.log_step("generate_loi", "blocked", error=str(exc))
            await self._set_status("draft", extra={"blocked_reason": str(exc)})
            return {"status": "draft", "error": "pdf_render_unavailable"}
        except TemplateVariableError as exc:
            # The customer's DOCX references an unknown {{ variable }}.
            # Block the run with an actionable message instead of failing
            # silently — HR sees "Template references undefined variable
            # 'manager_phone' — remove the placeholder or supply the value".
            await self.log_step(
                "generate_loi", "failed",
                error=str(exc),
                metadata={"missing_variable": exc.variable_name},
            )
            await self._set_status(
                "blocked_missing_template",
                extra={
                    "blocked_reason": str(exc),
                    "blocked_template_kind": "loi",
                },
            )
            await ob_storage.log_onboarding_event(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                actor_kind="agent",
                event_type="template_variable_error",
                message=str(exc),
                metadata={"template_kind": "loi", "variable": exc.variable_name},
            )
            return {"status": "blocked_missing_template", "variable": exc.variable_name}
        except PdfRenderError as exc:
            await self.log_step("generate_loi", "failed", error=str(exc))
            await self._set_status("failed", extra={"blocked_reason": str(exc)})
            raise

        storage = await ob_storage.upload_onboarding_artifact(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="loi",
            pdf_bytes=pdf_bytes,
            docx_bytes=filled_docx,
        )
        doc_id = await ob_storage.upsert_onboarding_document(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="loi",
            storage=storage,
            source_template_id=doc_row.get("id"),
            render_context=ctx,
            sign_status="draft",
        )

        await self._set_status("loi_pending_hr_review")
        await self.log_step(
            "generate_loi",
            "completed",
            {"document_id": doc_id, "size_bytes": storage["file_bytes"]},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="loi_ready_for_review",
            message=(
                f"LOI draft ready for HR review. Preview on the run page, "
                f"download to edit if needed, then click Send for signature."
            ),
            metadata={"document_id": doc_id},
        )
        return {"status": "loi_pending_hr_review", "document_id": doc_id}

    # ── Step 1b: HR has reviewed the draft → send for signature ────────────

    async def _step_send_to_hr_for_signature(self) -> dict[str, Any]:
        """Called after HR approves the LOI draft (POST .../loi/approve-draft).
        Stamps the signing timestamp, emails HR with the (possibly-edited)
        LOI PDF link, and parks the run in loi_pending_hr_sign waiting for
        the signed-scan upload."""
        await self.log_step("send_to_hr_for_signature", "started")
        run = await self._load_run()
        ctx = await self._build_render_context()

        # Resolve which PDF to send: HR's edited copy if present, else the
        # agent's original render. Edited copy lives at hr_edited_pdf_path.
        svc = get_service_client()
        doc = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select("id, storage_path, hr_edited_pdf_path")
            .eq("run_id", self.onboarding_run_id)
            .eq("kind", "loi")
            .maybe_single()
            .execute()
        )
        if not doc or not doc.data:
            await self.log_step(
                "send_to_hr_for_signature", "failed", error="loi_document_missing"
            )
            raise RuntimeError("loi_document_row_missing")

        pdf_path = doc.data.get("hr_edited_pdf_path") or doc.data["storage_path"]

        def _signed_url() -> str | None:
            try:
                res = svc.storage.from_(ob_storage.STORAGE_BUCKET).create_signed_url(
                    path=pdf_path,
                    expires_in=ob_storage.SIGNED_URL_TTL_SECONDS,
                )
                return res.get("signedURL") or res.get("signed_url")
            except Exception:
                return None

        signed_url = await asyncio.to_thread(_signed_url)
        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update({"sign_status": "sent_to_hr"})
            .eq("id", doc.data["id"])
            .execute()
        )

        now = datetime.now(UTC).isoformat()
        await self._set_status(
            "loi_pending_hr_sign", extra={"loi_sent_to_hr_at": now}
        )
        await self._notify_hr_loi_ready(
            ctx, {"signed_url": signed_url, "file_bytes": None}
        )
        await self.log_step("send_to_hr_for_signature", "completed")
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="loi_sent_to_hr_for_signature",
            message=(
                "HR approved the LOI draft — signing email dispatched. "
                "Awaiting scanned-signed-PDF upload."
            ),
            metadata={"used_edited_copy": bool(doc.data.get("hr_edited_pdf_path"))},
        )
        return {"status": "loi_pending_hr_sign", "document_id": doc.data["id"]}

    async def _notify_hr_loi_ready(
        self, ctx: dict[str, Any], storage: dict[str, Any]
    ) -> None:
        """Email HR (the user who triggered the run) with a download link to
        the LOI so they can sign + scan it back."""
        settings = get_settings()
        run = await self._load_run()
        triggered_by = run.get("triggered_by_user_id")
        if not triggered_by:
            await self.log_step("notify_hr_loi_ready", "skipped", {"reason": "no_hr"})
            return

        svc = get_service_client()
        try:
            au = await asyncio.to_thread(
                lambda: svc.auth.admin.get_user_by_id(triggered_by)
            )
            hr_email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            hr_email = None
        if not hr_email:
            await self.log_step("notify_hr_loi_ready", "skipped", {"reason": "no_hr_email"})
            return

        try:
            await send_email_event(
                event_type="onboarding_loi_ready",
                to=hr_email,
                user_id=triggered_by,
                org_id=self.org_id,
                dedupe_key=f"loi-ready-{self.onboarding_run_id}",
                data={
                    "candidate_name": ctx["candidate_name"],
                    "role_title": ctx["role_title"],
                    "ctc": ctx["ctc"],
                    "start_date": ctx["start_date"],
                    "loi_signed_url": storage.get("signed_url"),
                    "app_url": settings.app_url.rstrip("/"),
                    "run_id": self.onboarding_run_id,
                },
            )
            await self.log_step("notify_hr_loi_ready", "completed")
        except Exception as exc:  # noqa: BLE001
            await self.log_step("notify_hr_loi_ready", "failed", error=str(exc))

    async def _loi_esign_completed(self) -> bool:
        """True once apps/esign has marked the LOI envelope fully signed.
        apps/esign stamps onboarding_documents on the last signer (see
        apps/esign/app/routers/public_sign.py) — the run status is not touched."""
        svc = get_service_client()
        doc = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select("esign_status, sign_status")
            .eq("run_id", self.onboarding_run_id)
            .eq("kind", "loi")
            .maybe_single()
            .execute()
        )
        row = (doc.data if doc else None) or {}
        return (
            row.get("esign_status") == "completed"
            or row.get("sign_status") == "signed_by_candidate"
        )

    # ── Step 2: Send signed LOI to candidate ───────────────────────────────

    async def _step_send_loi_to_candidate(self) -> dict[str, Any]:
        await self.log_step("send_loi_to_candidate", "started")
        run = await self._load_run()
        ctx = await self._build_render_context()
        settings = get_settings()

        # Look up the signed-by-HR LOI; HR's upload handler sets this path.
        svc = get_service_client()
        doc = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select("id, storage_path, signed_pdf_path")
            .eq("run_id", self.onboarding_run_id)
            .eq("kind", "loi")
            .maybe_single()
            .execute()
        )
        if not doc or not doc.data:
            await self.log_step(
                "send_loi_to_candidate",
                "failed",
                error="loi_document_row_missing",
            )
            raise RuntimeError("loi_document_row_missing")

        # Prefer the HR-signed PDF; fall back to the generated draft if HR
        # skipped the upload step (e.g. running a dry-run flow).
        signed_path = doc.data.get("signed_pdf_path") or doc.data["storage_path"]

        def _signed_url() -> str | None:
            try:
                res = svc.storage.from_(ob_storage.STORAGE_BUCKET).create_signed_url(
                    path=signed_path,
                    expires_in=ob_storage.SIGNED_URL_TTL_SECONDS,
                )
                return res.get("signedURL") or res.get("signed_url")
            except Exception:
                return None

        signed_url = await asyncio.to_thread(_signed_url)

        # Mint the public-form token the candidate uses to submit BGV refs.
        # 14-day expiry matches the existing referee-token convention so the
        # cron reminder window has room to fire.
        from uuid import uuid4
        from datetime import timedelta
        refs_token = str(uuid4())
        refs_expires_at = (datetime.now(UTC) + timedelta(days=14)).isoformat()
        refs_form_url = (
            f"{settings.app_url.rstrip('/')}/references/{refs_token}"
            if settings.app_url else None
        )

        try:
            await send_email_event(
                event_type="onboarding_loi_to_candidate",
                to=run["candidate_email"],
                user_id=None,
                org_id=self.org_id,
                dedupe_key=f"loi-cand-{self.onboarding_run_id}",
                data={
                    "candidate_name": ctx["candidate_name"],
                    "role_title": ctx["role_title"],
                    "company_name": ctx["company_name"],
                    "start_date": ctx["start_date"],
                    "loi_signed_url": signed_url,
                    "references_form_url": refs_form_url,
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self.log_step("send_loi_to_candidate", "failed", error=str(exc))
            raise

        now = datetime.now(UTC).isoformat()
        await self._set_status(
            "loi_sent_to_candidate",
            extra={
                "loi_sent_to_candidate_at": now,
                "references_form_token": refs_token,
                "references_form_expires_at": refs_expires_at,
            },
        )
        await self.log_step("send_loi_to_candidate", "completed")
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="loi_sent_to_candidate",
            message=(
                f"Signed LOI emailed to {run['candidate_email']}. "
                "Candidate was asked to submit BGV references via the form link."
            ),
        )

        # Provision the pre-join users row + send the candidate's magic link
        # so the policy step (further down the pipeline) has a user_id to
        # attach acknowledgements to. Best-effort — failures here don't
        # block the run; the policy step will retry.
        try:
            user_id = await ensure_pre_join_user(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                candidate_email=run["candidate_email"],
                candidate_name=run["candidate_name"],
            )
            settings = get_settings()
            await send_magic_link(
                email=run["candidate_email"],
                redirect_to=(
                    f"{settings.app_url.rstrip('/')}/compliance"
                    if settings.app_url else None
                ),
            )
            await ob_storage.log_onboarding_event(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                actor_kind="agent",
                event_type="pre_join_user_provisioned",
                message="Pre-join user account created and magic-link sent.",
                metadata={"user_id": user_id},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "onboarding_v2.pre_join_provision_failed run=%s err=%s",
                self.onboarding_run_id,
                exc,
            )

        # Park in awaiting_candidate_references — BGV kicks off only after
        # the candidate (or an HR override) writes refs and re-kicks the agent.
        await self._set_status("awaiting_candidate_references")
        return {
            "status": "awaiting_candidate_references",
            "waiting_for": "candidate_to_submit_references",
        }

    # ── Step 2b: wait for candidate references ─────────────────────────────

    async def _step_wait_for_candidate_references(self) -> dict[str, Any]:
        """Park until the candidate submits the references form. The public
        route at /api/public/onboarding/references/{token} writes the refs
        and stamps references_submitted_at, then re-kicks the agent. HR can
        also force-submit via /runs/{id}/references-override (same effect)."""
        run = await self._load_run()
        if run.get("references_submitted_at"):
            return await self._step_kick_off_bgv()
        return {
            "status": "awaiting_candidate_references",
            "waiting_for": "candidate_to_submit_references",
        }

    # ── Step 3: BGV ────────────────────────────────────────────────────────

    async def _step_kick_off_bgv(self) -> dict[str, Any]:
        await self.log_step("kick_off_bgv", "started")
        run = await self._load_run()
        settings = get_settings()
        base_url = (
            settings.bgv_public_url.rstrip("/") if settings.bgv_public_url
            else settings.app_url.rstrip("/")
        )

        svc = get_service_client()
        refs = await asyncio.to_thread(
            lambda: svc.table("onboarding_bgv_references")
            .select("id, token, reference_email, reference_name, status")
            .eq("run_id", self.onboarding_run_id)
            .execute()
        )
        ref_rows: list[dict[str, Any]] = refs.data or []
        if not ref_rows:
            # No references entered — surface as a blocked state HR can fix.
            await self.log_step(
                "kick_off_bgv", "skipped", {"reason": "no_references_entered"}
            )
            await self._set_status(
                "failed",
                extra={"blocked_reason": "no_bgv_references_entered"},
            )
            return {"status": "failed", "reason": "no_bgv_references_entered"}

        sent_count = 0
        for ref in ref_rows:
            if ref.get("status") not in (None, "pending"):
                continue
            url = f"{base_url}/bgv/{ref['token']}"
            try:
                await send_email_event(
                    event_type="onboarding_bgv_request",
                    to=ref["reference_email"],
                    user_id=None,
                    org_id=self.org_id,
                    dedupe_key=f"bgv-req-{ref['id']}",
                    data={
                        "reference_name": ref["reference_name"],
                        "candidate_name": run["candidate_name"],
                        "company_name": (await self._resolve_org_name()),
                        "role_title": run["role_title"],
                        "form_url": url,
                    },
                )
                await asyncio.to_thread(
                    lambda r=ref: svc.table("onboarding_bgv_references")
                    .update(
                        {
                            "status": "sent",
                            "email_sent_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    .eq("id", r["id"])
                    .execute()
                )
                sent_count += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "onboarding_v2.bgv_email_failed ref=%s err=%s", ref["id"], exc
                )

        now = datetime.now(UTC).isoformat()
        await self._set_status("bgv_pending", extra={"bgv_sent_at": now})
        await self.log_step(
            "kick_off_bgv", "completed", {"sent_count": sent_count}
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="bgv_emails_sent",
            message=f"Verification emails sent to {sent_count} reference(s).",
            metadata={"sent_count": sent_count},
        )
        return {"status": "bgv_pending", "sent_count": sent_count}

    async def _step_check_bgv_completion(self) -> dict[str, Any]:
        """Called from the BGV-response Inngest handler. If all references
        have responded, transition forward."""
        svc = get_service_client()
        refs = await asyncio.to_thread(
            lambda: svc.table("onboarding_bgv_references")
            .select("status")
            .eq("run_id", self.onboarding_run_id)
            .execute()
        )
        rows = refs.data or []
        if not rows:
            return {"status": "bgv_pending", "no_refs": True}
        if any(r.get("status") != "submitted" for r in rows):
            return {"status": "bgv_pending", "waiting": True}

        now = datetime.now(UTC).isoformat()
        await self._set_status("bgv_complete", extra={"bgv_completed_at": now})
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="bgv_complete",
            message="All references have responded.",
        )
        return await self._step_generate_offer_bundle()

    # ── Step 4: Appointment Letter + NDA bundle ────────────────────────────

    async def _step_generate_offer_bundle(self) -> dict[str, Any]:
        await self.log_step("generate_offer_bundle", "started")

        # Verify BOTH templates exist BEFORE flipping status — see comment
        # in _step_generate_loi for why we check first.
        fetched_by_kind: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for kind in ("appointment_letter", "nda"):
            fetched = await ob_storage.fetch_template_docx(
                org_id=self.org_id, template_kind=kind
            )
            if not fetched:
                await self.log_step(
                    "generate_offer_bundle",
                    "skipped",
                    {"reason": f"no_template:{kind}"},
                )
                return await self._block_missing_template(kind)
            fetched_by_kind[kind] = fetched

        await self._set_status("appointment_bundle_generating")
        ctx = await self._build_render_context()

        for kind, label in (
            ("appointment_letter", "Appointment Letter"),
            ("nda", "NDA"),
        ):
            fetched = fetched_by_kind[kind]

            docx_bytes, doc_row = fetched
            try:
                filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                    template_bytes=docx_bytes,
                    context=ctx,
                    strict=True,
                    template_kind=kind,
                )
            except TemplateVariableError as exc:
                await self.log_step(
                    "generate_offer_bundle", "failed",
                    error=f"{kind}: {exc}",
                    metadata={"missing_variable": exc.variable_name, "kind": kind},
                )
                await self._set_status(
                    "blocked_missing_template",
                    extra={
                        "blocked_reason": str(exc),
                        "blocked_template_kind": kind,
                    },
                )
                await ob_storage.log_onboarding_event(
                    org_id=self.org_id,
                    run_id=self.onboarding_run_id,
                    actor_kind="agent",
                    event_type="template_variable_error",
                    message=str(exc),
                    metadata={"template_kind": kind, "variable": exc.variable_name},
                )
                return {"status": "blocked_missing_template", "variable": exc.variable_name}
            except PdfRenderUnavailable as exc:
                await self.log_step("generate_offer_bundle", "blocked", error=f"{kind}: {exc}")
                await self._set_status("bgv_complete", extra={"blocked_reason": str(exc)})
                return {"status": "bgv_complete", "error": "pdf_render_unavailable"}
            except PdfRenderError as exc:
                await self.log_step(
                    "generate_offer_bundle", "failed", error=f"{kind}: {exc}"
                )
                await self._set_status("failed", extra={"blocked_reason": str(exc)})
                raise

            storage = await ob_storage.upload_onboarding_artifact(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                kind=kind,
                pdf_bytes=pdf_bytes,
                docx_bytes=filled_docx,
            )
            await ob_storage.upsert_onboarding_document(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                kind=kind,
                storage=storage,
                source_template_id=doc_row.get("id"),
                render_context=ctx,
                sign_status="sent_to_hr",
            )
            await ob_storage.log_onboarding_event(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                actor_kind="agent",
                event_type=f"{kind}_generated",
                message=f"{label} generated.",
            )

        await self._set_status("appointment_pending_hr_review")
        await self.log_step("generate_offer_bundle", "completed")
        await self._notify_hr_bundle_ready(ctx)
        return {"status": "appointment_pending_hr_review"}

    async def _notify_hr_bundle_ready(self, ctx: dict[str, Any]) -> None:
        run = await self._load_run()
        triggered_by = run.get("triggered_by_user_id")
        if not triggered_by:
            return
        svc = get_service_client()
        try:
            au = await asyncio.to_thread(
                lambda: svc.auth.admin.get_user_by_id(triggered_by)
            )
            hr_email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            hr_email = None
        if not hr_email:
            return
        settings = get_settings()
        try:
            await send_email_event(
                event_type="onboarding_offer_bundle_ready",
                to=hr_email,
                user_id=triggered_by,
                org_id=self.org_id,
                dedupe_key=f"bundle-ready-{self.onboarding_run_id}",
                data={
                    "candidate_name": ctx["candidate_name"],
                    "role_title": ctx["role_title"],
                    "app_url": settings.app_url.rstrip("/"),
                    "run_id": self.onboarding_run_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.notify_hr_bundle_failed err=%s", exc)

    # ── Step 5: Policies ───────────────────────────────────────────────────

    async def _step_assign_policies(self) -> dict[str, Any]:
        """Pull policy docs from the KB via search and create acknowledgement
        rows for the candidate. Reuses the compliance system from migration
        031 — the dashboard ack UI works as-is.

        The candidate's pre_join users row was provisioned at LOI-sent time
        (see `_step_send_loi_to_candidate` → `ensure_pre_join_user`). If
        provisioning had failed back then, retry it now — we'd rather slow
        the run by one round-trip than block on it.
        """
        await self.log_step("assign_policies", "started")
        svc = get_service_client()
        run = await self._load_run()

        user_id = run.get("pre_join_user_id")
        if not user_id:
            # Provisioning didn't run at LOI sign time (e.g. failed); retry
            # now. If it fails again, we surface a clean failure rather than
            # silently stalling.
            try:
                user_id = await ensure_pre_join_user(
                    org_id=self.org_id,
                    run_id=self.onboarding_run_id,
                    candidate_email=run["candidate_email"],
                    candidate_name=run["candidate_name"],
                )
            except Exception as exc:  # noqa: BLE001
                await self.log_step(
                    "assign_policies", "failed",
                    error=f"pre_join_provisioning_failed: {exc}",
                )
                await self._set_status(
                    "failed",
                    extra={"blocked_reason": f"pre_join_provisioning_failed: {exc}"},
                )
                raise

        # Find policy docs: any with template_kind not set AND tag/flag-based
        # selection. The simplest reliable signal in this codebase is
        # `requires_acknowledgement=true`. Skip our own generated artifacts.
        policy_docs = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, name, current_version_id")
            .eq("org_id", self.org_id)
            .eq("requires_acknowledgement", True)
            .is_("template_kind", "null")
            .execute()
        )
        rows = policy_docs.data or []
        if not rows:
            await self.log_step(
                "assign_policies", "skipped", {"reason": "no_policy_docs"}
            )
            await self._set_status(
                "policies_acknowledged",
                extra={"policies_acknowledged_at": datetime.now(UTC).isoformat()},
            )
            return await self._step_generate_induction()

        inserted = 0
        for d in rows:
            payload = {
                "org_id": self.org_id,
                "document_id": d["id"],
                "document_version_id": d.get("current_version_id"),
                "user_id": user_id,
                "status": "pending",
            }
            try:
                await asyncio.to_thread(
                    lambda p=payload: svc.table("acknowledgements")
                    .insert(p)
                    .execute()
                )
                inserted += 1
            except Exception as exc:
                # Duplicate (already assigned) — skip silently.
                if "duplicate" not in str(exc).lower():
                    log.warning(
                        "onboarding_v2.ack_insert_failed doc=%s err=%s",
                        d["id"],
                        exc,
                    )

        await self._set_status(
            "policies_assigned",
            extra={"policies_assigned_at": datetime.now(UTC).isoformat()},
        )
        await self.log_step(
            "assign_policies",
            "completed",
            {"assigned_count": inserted, "total_policies": len(rows)},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="policies_assigned",
            message=f"Assigned {inserted} policy acknowledgement(s).",
            metadata={"count": inserted},
        )

        # Notify candidate to acknowledge.
        settings = get_settings()
        try:
            await send_email_event(
                event_type="onboarding_policies_pending",
                to=run["candidate_email"],
                user_id=user_id,
                org_id=self.org_id,
                dedupe_key=f"policies-{self.onboarding_run_id}",
                data={
                    "candidate_name": run["candidate_name"],
                    "policy_count": inserted,
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.policy_email_failed err=%s", exc)

        return {"status": "policies_assigned", "assigned": inserted}

    async def _step_check_policy_acks(self) -> dict[str, Any]:
        """Called from the compliance ack handler. Move forward if all
        assigned policies are acknowledged."""
        svc = get_service_client()
        run = await self._load_run()

        user_id = run.get("pre_join_user_id")
        if not user_id:
            return {"status": "policies_assigned", "waiting_for": "user_row"}

        pending = await asyncio.to_thread(
            lambda: svc.table("acknowledgements")
            .select("id", count="exact")
            .eq("org_id", self.org_id)
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        if (pending.count or 0) > 0:
            return {"status": "policies_assigned", "pending": pending.count}

        await self._set_status(
            "policies_acknowledged",
            extra={"policies_acknowledged_at": datetime.now(UTC).isoformat()},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="policies_acknowledged",
            message="All policy acknowledgements complete.",
        )
        return await self._step_generate_induction()

    # ── Step 6: Induction PDF ──────────────────────────────────────────────

    async def _step_generate_induction(self) -> dict[str, Any]:
        """Render the org's KB-tagged induction DOCX template with the same
        variable-substitution pipeline used for LOI / AL / NDA. If no
        template is tagged, block and prompt HR to upload one — we never
        synthesize induction content."""
        await self.log_step("generate_induction", "started")

        fetched = await ob_storage.fetch_template_docx(
            org_id=self.org_id, template_kind="induction"
        )
        if not fetched:
            await self.log_step(
                "generate_induction",
                "skipped",
                {"reason": "no_template:induction"},
            )
            return await self._block_missing_template("induction")

        await self._set_status("induction_generating")
        run = await self._load_run()
        ctx = await self._build_render_context()
        docx_bytes, doc_row = fetched

        try:
            filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                template_bytes=docx_bytes,
                context=ctx,
                strict=True,
                template_kind="induction",
            )
        except TemplateVariableError as exc:
            await self.log_step(
                "generate_induction", "failed",
                error=str(exc),
                metadata={"missing_variable": exc.variable_name},
            )
            await self._set_status(
                "blocked_missing_template",
                extra={
                    "blocked_reason": str(exc),
                    "blocked_template_kind": "induction",
                },
            )
            await ob_storage.log_onboarding_event(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                actor_kind="agent",
                event_type="template_variable_error",
                message=str(exc),
                metadata={"template_kind": "induction", "variable": exc.variable_name},
            )
            return {"status": "blocked_missing_template", "variable": exc.variable_name}
        except PdfRenderUnavailable as exc:
            await self.log_step("generate_induction", "blocked", error=str(exc))
            await self._set_status("policies_acknowledged", extra={"blocked_reason": str(exc)})
            return {"status": "policies_acknowledged", "error": "pdf_render_unavailable"}
        except PdfRenderError as exc:
            await self.log_step("generate_induction", "failed", error=str(exc))
            await self._set_status("failed", extra={"blocked_reason": str(exc)})
            raise

        storage = await ob_storage.upload_onboarding_artifact(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="induction",
            pdf_bytes=pdf_bytes,
            docx_bytes=filled_docx,
        )
        doc_id = await ob_storage.upsert_onboarding_document(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="induction",
            storage=storage,
            source_template_id=doc_row.get("id"),
            render_context=ctx,
            sign_status="sent_to_candidate",
        )

        settings = get_settings()
        try:
            await send_email_event(
                event_type="onboarding_induction_ready",
                to=run["candidate_email"],
                user_id=None,
                org_id=self.org_id,
                dedupe_key=f"induction-{self.onboarding_run_id}",
                data={
                    "candidate_name": ctx["candidate_name"],
                    "role_title": ctx["role_title"],
                    "company_name": ctx["company_name"],
                    "start_date": ctx["start_date"],
                    "induction_signed_url": storage.get("signed_url"),
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.induction_email_failed err=%s", exc)

        await self._set_status(
            "induction_sent",
            extra={"induction_sent_at": datetime.now(UTC).isoformat()},
        )
        await self.log_step(
            "generate_induction",
            "completed",
            {"document_id": doc_id},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="induction_sent",
            message="Induction PDF sent to candidate.",
        )
        return await self._step_finalise()

    async def _step_finalise(self) -> dict[str, Any]:
        await self._set_status(
            "completed",
            extra={"completed_at": datetime.now(UTC).isoformat()},
        )
        await self.log_step("finalise", "completed")
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="onboarding_complete",
            message="Onboarding pipeline complete.",
        )
        return {"status": "completed"}
