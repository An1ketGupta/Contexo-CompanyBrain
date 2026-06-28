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
  loi_pending_hr_sign  ── HR downloads PDF, prints, signs, scans, uploads
    │
    ▼
  loi_signed_uploaded
    │
    ▼
  loi_sent_to_candidate
    │
    ▼
  bgv_pending  ── 2 references each get a tokenised URL; wait for responses
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
  induction_generating  ── LLM-synthesized from N facet searches
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
import logging
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.services.agents.base_agent import BaseAgent
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.agents.onboarding_v2.induction_template import (
    render_induction_html,
)
from app.services.email import send_email_event
from app.services.pdf import (
    PdfRenderError,
    PdfRenderUnavailable,
    render_docx_template_to_pdf,
    render_html_to_pdf,
)

log = logging.getLogger(__name__)


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
        payload: dict[str, Any] = {"status": status, "current_step": status}
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
                "blocked_reason": f"missing_template:{template_kind}",
                "blocked_template_kind": template_kind,
            },
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="blocked_missing_template",
            message=(
                f"Upload your {template_kind.replace('_', ' ')} template "
                "to the knowledge base and tag it to continue."
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

    # ── Render context ────────────────────────────────────────────────────

    async def _build_render_context(self) -> dict[str, Any]:
        """The dict handed to docxtpl for placeholder substitution. Tracks
        the variables a customer template can reference. Documented in
        apps/api/tools/ONBOARDING_TEMPLATE_VARS.md (to be added)."""
        run = await self._load_run()
        org_name = await self._resolve_org_name()
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
            "company_name": org_name,
            "today_date": today,
            "jurisdiction": "India",
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

        if current in ("draft", "loi_generating"):
            return await self._step_generate_loi()
        if current == "loi_pending_hr_sign":
            return {"status": current, "waiting_for": "hr_to_upload_signed_loi"}
        if current == "loi_signed_uploaded":
            return await self._step_send_loi_to_candidate()
        if current == "loi_sent_to_candidate":
            return await self._step_kick_off_bgv()
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

        return {"status": current, "no_op": True}

    # ── Step 1: LOI ────────────────────────────────────────────────────────

    async def _step_generate_loi(self) -> dict[str, Any]:
        await self._set_status("loi_generating")
        await self.log_step("generate_loi", "started")

        fetched = await ob_storage.fetch_template_docx(
            org_id=self.org_id, template_kind="loi"
        )
        if not fetched:
            await self.log_step("generate_loi", "skipped", {"reason": "no_template"})
            return await self._block_missing_template("loi")

        docx_bytes, doc_row = fetched
        ctx = await self._build_render_context()

        try:
            filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                template_bytes=docx_bytes, context=ctx
            )
        except PdfRenderUnavailable as exc:
            await self.log_step("generate_loi", "failed", error=str(exc))
            await self._set_status("failed", extra={"blocked_reason": str(exc)})
            raise
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
            sign_status="sent_to_hr",
        )

        now = datetime.now(UTC).isoformat()
        await self._set_status(
            "loi_pending_hr_sign", extra={"loi_sent_to_hr_at": now}
        )
        await self.log_step(
            "generate_loi",
            "completed",
            {"document_id": doc_id, "size_bytes": storage["file_bytes"]},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="loi_generated",
            message=f"LOI generated for {ctx['candidate_name']}.",
            metadata={"document_id": doc_id},
        )

        await self._notify_hr_loi_ready(ctx, storage)
        return {"status": "loi_pending_hr_sign", "document_id": doc_id}

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
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            await self.log_step("send_loi_to_candidate", "failed", error=str(exc))
            raise

        now = datetime.now(UTC).isoformat()
        await self._set_status(
            "loi_sent_to_candidate", extra={"loi_sent_to_candidate_at": now}
        )
        await self.log_step("send_loi_to_candidate", "completed")
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="loi_sent_to_candidate",
            message=f"Signed LOI emailed to {run['candidate_email']}.",
        )
        # Auto-advance to BGV — no human gate here.
        return await self._step_kick_off_bgv()

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
        await self._set_status("appointment_bundle_generating")
        await self.log_step("generate_offer_bundle", "started")
        ctx = await self._build_render_context()

        for kind, label in (
            ("appointment_letter", "Appointment Letter"),
            ("nda", "NDA"),
        ):
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

            docx_bytes, doc_row = fetched
            try:
                filled_docx, pdf_bytes = await render_docx_template_to_pdf(
                    template_bytes=docx_bytes, context=ctx
                )
            except (PdfRenderError, PdfRenderUnavailable) as exc:
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

        Note: the candidate isn't a `users` row yet (they haven't accepted an
        invite). We surface this as a soft-fail and let HR send the invite
        from the onboarding detail page; the agent gets re-kicked once the
        user_id exists.
        """
        await self.log_step("assign_policies", "started")
        svc = get_service_client()
        run = await self._load_run()

        # Look up users-row by candidate_email — created when HR sends the
        # employee invite. If no row exists, surface a soft block.
        user_row = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id")
            .eq("org_id", self.org_id)
            .ilike("email", run["candidate_email"])
            .maybe_single()
            .execute()
        )
        if not user_row or not user_row.data:
            await self.log_step(
                "assign_policies",
                "skipped",
                {"reason": "candidate_user_row_missing"},
            )
            # Don't move forward — we'll be re-kicked when the invite is sent.
            return {
                "status": "appointment_sent_to_candidate",
                "waiting_for": "candidate_user_row",
            }
        user_id = user_row.data["id"]

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

        user_row = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id")
            .eq("org_id", self.org_id)
            .ilike("email", run["candidate_email"])
            .maybe_single()
            .execute()
        )
        if not user_row or not user_row.data:
            return {"status": "policies_assigned", "waiting_for": "user_row"}
        user_id = user_row.data["id"]

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
        await self._set_status("induction_generating")
        await self.log_step("generate_induction", "started")
        run = await self._load_run()
        ctx = await self._build_render_context()

        facets = {
            "culture": "company culture values mission principles",
            "team": "team structure org chart leadership",
            "tools": "tools and software we use day to day",
            "processes": "processes workflows ways of working",
            "policies": "rules regulations expectations leave benefits",
            "role_specific": f"{ctx['role_title']} responsibilities expectations",
        }
        facet_results = await search_facets_concurrent(
            org_id=self.org_id, facets=facets, k=6, char_budget_per_facet=3500
        )
        context_block = build_context_block(facet_results)
        sources = collect_sources(facet_results)

        if not context_block.strip():
            await self.log_step(
                "generate_induction",
                "failed",
                error="no_kb_content_for_induction",
            )
            await self._set_status(
                "failed",
                extra={"blocked_reason": "kb_empty_cannot_generate_induction"},
            )
            return {"status": "failed", "reason": "kb_empty"}

        system_prompt = (
            "You are NirnayaIQ writing a personalised induction document for a "
            "new hire. Use ONLY facts found in the provided knowledge-base "
            "context. Where a section has no relevant context, write a short "
            "honest note 'Your manager will brief you on this in your first 1:1.' "
            "Return STRICT JSON only, no prose around it."
        )
        user_prompt = (
            f"Hire: {ctx['candidate_name']}\n"
            f"Role: {ctx['role_title']}\n"
            f"Company: {ctx['company_name']}\n"
            f"Start date: {ctx['start_date']}\n\n"
            "Generate the induction as JSON of shape:\n"
            "{\n"
            '  "sections": [\n'
            '    {"heading": "Welcome from the team", "body": "..."},\n'
            '    {"heading": "How we work", "body": "..."},\n'
            '    {"heading": "Your first two weeks", "body": "..."},\n'
            '    {"heading": "Tools you\'ll use", "body": "..."},\n'
            '    {"heading": "Who to talk to about what", "body": "..."},\n'
            '    {"heading": "Rules and expectations", "body": "..."},\n'
            '    {"heading": "Where to learn more", "body": "..."}\n'
            "  ]\n"
            "}\n\n"
            "Each `body` is plain text — use blank lines for paragraphs and "
            "lines starting with `- ` for bullets. Keep each section 4-8 short "
            "paragraphs or a tight bullet list. Warm, practical tone.\n\n"
            "=== Knowledge Base Context ===\n"
            f"{context_block}\n"
        )

        try:
            llm_out = await synthesize_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4,
                timeout=120.0,
            )
        except Exception as exc:  # noqa: BLE001
            await self.log_step(
                "generate_induction", "failed", error=f"llm_error: {exc}"
            )
            raise

        sections = (
            llm_out.get("sections", [])
            if isinstance(llm_out, dict)
            else (llm_out if isinstance(llm_out, list) else [])
        )

        html = render_induction_html(
            candidate_name=ctx["candidate_name"],
            role_title=ctx["role_title"],
            org_name=ctx["company_name"],
            start_date=ctx["start_date"],
            sections=sections,
            sources=sources,
        )

        try:
            pdf_bytes = await render_html_to_pdf(html)
        except (PdfRenderError, PdfRenderUnavailable) as exc:
            await self.log_step("generate_induction", "failed", error=str(exc))
            await self._set_status("failed", extra={"blocked_reason": str(exc)})
            raise

        storage = await ob_storage.upload_onboarding_artifact(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="induction",
            pdf_bytes=pdf_bytes,
        )
        doc_id = await ob_storage.upsert_onboarding_document(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            kind="induction",
            storage=storage,
            source_template_id=None,
            render_context={
                "section_count": len(sections),
                "source_count": len(sources),
            },
            sign_status="sent_to_candidate",
        )

        # Email candidate.
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
            {"document_id": doc_id, "sections": len(sections)},
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
