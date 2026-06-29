"""SalesAgent — autonomous deal pipeline state machine.

Re-entrant agent that walks a single `deal_runs` row from `lead_entered`
through to `closed_won` / `closed_lost`. Mirrors the OnboardingV2Agent
pattern but bespoke (no shared base pipeline class — per the
architectural decision in Sales_Agent.md).

Human-in-the-loop gates:
  * outreach_pending_rep_review — cold + follow-up + check-in drafts
  * proposal_pending_rep_review — proposal/SOW/pricing artifacts
  * awaiting_next_step_decision — propose vs another_call vs qualify_out
    after each call

Re-entry: `run()` reads `deal_runs.status` and dispatches. Steps that
wait on a human exit without raising — the run is re-kicked by the
relevant approval / event router.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.agents.sales_agent import (
    call_summary as cs_svc,
    followups as fu_svc,
    outreach as outreach_svc,
    prep as prep_svc,
    proposal as proposal_svc,
    research as research_svc,
    storage as sa_storage,
)
from app.services.pdf import (
    PdfRenderError,
    PdfRenderUnavailable,
    TemplateVariableError,
)

log = get_logger(__name__)


class SalesAgent(BaseAgent):
    agent_type = "sales"

    def __init__(
        self,
        *,
        org_id: str,
        deal_run_id: str,
        triggered_by_user_id: str | None = None,
        resume_from: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(
            org_id=org_id,
            input_data={"deal_run_id": deal_run_id, "resume_from": resume_from},
            triggered_by="webhook",
            triggered_by_user_id=triggered_by_user_id,
            run_id=run_id,
        )
        self.deal_run_id = deal_run_id
        self.resume_from = resume_from
        self._deal_row: dict[str, Any] | None = None
        self.log = log.bind(deal_run_id=deal_run_id, org_id=org_id, agent="sales")

    # ── State helpers ──────────────────────────────────────────────────────

    async def _load_deal(self) -> dict[str, Any]:
        if self._deal_row is not None:
            return self._deal_row
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("deal_runs")
            .select("*")
            .eq("id", self.deal_run_id)
            .maybe_single()
            .execute()
        )
        if not res or not res.data:
            raise RuntimeError(f"deal_run_not_found:{self.deal_run_id}")
        self._deal_row = res.data
        return res.data

    async def _refresh_deal(self) -> dict[str, Any]:
        self._deal_row = None
        return await self._load_deal()

    async def _set_status(
        self,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
        log_event: bool = True,
        event_message: str | None = None,
    ) -> None:
        svc = get_service_client()
        cur = self._deal_row or await self._load_deal()
        from_status = cur.get("status")
        payload: dict[str, Any] = {
            "status": status,
            "current_step": status,
            "previous_status": from_status if from_status != status else cur.get("previous_status"),
            "blocked_reason": None,
            "blocked_template_kind": None,
        }
        if extra:
            payload.update(extra)
        await asyncio.to_thread(
            lambda: svc.table("deal_runs")
            .update(payload)
            .eq("id", self.deal_run_id)
            .execute()
        )
        self._deal_row = None
        if log_event:
            await sa_storage.log_deal_event(
                org_id=self.org_id,
                deal_run_id=self.deal_run_id,
                actor_kind="agent",
                event_type="status_transition",
                message=event_message,
                from_status=from_status,
                to_status=status,
            )

    async def _block_missing_template(
        self,
        template_kind: str,
        *,
        status: str = "blocked_missing_template",
    ) -> dict[str, Any]:
        await self._set_status(
            status,
            extra={
                "blocked_reason": (
                    f"No {template_kind.replace('_', ' ')} template uploaded. "
                    "Configure it in Sales → Templates."
                ),
                "blocked_template_kind": template_kind,
            },
            event_message=f"Blocked: missing template {template_kind}",
        )
        return {"status": status, "missing": template_kind}

    async def _resolve_org_branding(self) -> dict[str, Any]:
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

    # ── Main dispatcher ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        deal = await self._load_deal()
        current = deal.get("status") or "lead_entered"
        await self.log_step(
            "dispatch",
            "started",
            {"current_status": current, "resume_from": self.resume_from},
        )

        if current in ("closed_won", "closed_lost", "cancelled", "failed"):
            return {"status": current, "terminal": True}

        # Stage 1 — research / ICP
        if current in ("lead_entered", "researching"):
            return await self._step_research_and_score()
        if current == "icp_scoring":
            return await self._step_research_and_score()
        if current == "icp_scored":
            return await self._step_draft_outreach()

        # Stage 2 — outreach drafting + review gate
        if current == "outreach_drafting":
            return await self._step_draft_outreach()
        if current == "outreach_pending_rep_review":
            return {"status": current, "waiting_for": "rep_to_approve_outreach"}

        # Stage 2 → 3
        if current == "outreach_sent":
            return await self._step_after_outreach_sent()
        if current == "awaiting_reply":
            return {"status": current, "waiting_for": "prospect_reply_or_followup_cron"}
        if current == "followup_pending_rep_review":
            return {"status": current, "waiting_for": "rep_to_approve_followup"}

        # Stage 4 — meeting + prep
        if current == "meeting_booked":
            return await self._step_generate_prep_brief()
        if current == "prep_generating":
            return await self._step_generate_prep_brief()
        if current == "prep_ready":
            return {"status": current, "waiting_for": "rep_to_attend_meeting"}

        # Stage 5 — call summary
        if current == "call_summarizing":
            return {"status": current, "waiting_for": "transcript_processing"}
        if current == "call_summarized":
            return await self._step_request_next_step_decision()
        if current == "awaiting_next_step_decision":
            return {"status": current, "waiting_for": "rep_to_choose_next_step"}

        # Stage 6 — proposal
        if current == "proposal_drafting":
            return await self._step_generate_proposal()
        if current == "proposal_pending_rep_review":
            return {"status": current, "waiting_for": "rep_to_approve_proposal"}
        if current == "proposal_sent":
            return await self._step_after_proposal_sent()

        # Stage 7 — awaiting decision / check-ins
        if current == "awaiting_decision":
            return {"status": current, "waiting_for": "prospect_decision_or_checkin_cron"}
        if current == "checkin_pending_rep_review":
            return {"status": current, "waiting_for": "rep_to_approve_checkin"}
        if current == "at_risk":
            return {"status": current, "waiting_for": "rep_intervention"}

        # Blocked re-entry
        if current.startswith("blocked_"):
            kind = (deal.get("blocked_template_kind") or "").lower()
            if current == "blocked_missing_icp_doc":
                return await self._step_research_and_score()
            if kind == "proposal":
                return await self._step_generate_proposal()
            # Re-attempt the most recent step that triggered the block.
            previous = deal.get("previous_status")
            if previous:
                await self._set_status(previous, event_message="resume from blocked")
                return await self.run()

        return {"status": current, "no_op": True}

    # ── Stage 1: research + ICP ───────────────────────────────────────────

    async def _step_research_and_score(self) -> dict[str, Any]:
        """KB research → Tavily fallback → persist deal_research → ICP scoring."""
        await self.log_step("research", "started")
        await self._set_status("researching")
        deal = await self._refresh_deal()

        # ICP doc is required upstream.
        icp_doc = await sa_storage.fetch_kb_doc_by_kind(
            org_id=self.org_id, template_kind="icp"
        )
        if not icp_doc:
            await self.log_step("research", "blocked", {"reason": "no_icp_doc"})
            await self._block_missing_template("icp", status="blocked_missing_icp_doc")
            return {"status": "blocked_missing_icp_doc"}

        research_payload = await research_svc.research_company(
            org_id=self.org_id,
            company_name=deal["company_name"],
            company_website=deal.get("company_website"),
            industry_hint=deal.get("company_industry"),
            contact_name=deal.get("contact_name"),
            contact_title=deal.get("contact_title"),
        )
        source = research_payload.pop("source", "kb")

        research_id = await sa_storage.insert_deal_research(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            source=source,
            payload=research_payload,
        )
        await self.log_step(
            "research",
            "completed",
            {"research_id": research_id, "source": source},
        )

        await self._set_status("icp_scoring")
        scored = await research_svc.score_icp(
            org_id=self.org_id,
            company_name=deal["company_name"],
            research=research_payload,
            icp_doc_id=icp_doc["id"],
        )
        await self.log_step("icp_score", "completed", scored)

        await self._set_status(
            "icp_scored",
            extra={
                "icp_score": scored.get("score"),
                "icp_rationale": scored.get("rationale"),
                "icp_scored_at": datetime.now(UTC).isoformat(),
            },
        )
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="icp_scored",
            message=f"ICP score: {scored.get('score')} — {scored.get('rationale')}",
            payload={"score": scored.get("score")},
        )

        # Flow forward into outreach drafting.
        return await self._step_draft_outreach()

    # ── Stage 2: outreach draft + gate ────────────────────────────────────

    async def _step_draft_outreach(self) -> dict[str, Any]:
        await self.log_step("draft_outreach", "started")
        await self._set_status("outreach_drafting")
        deal = await self._refresh_deal()

        research = await sa_storage.fetch_latest_research(self.deal_run_id) or {}
        draft = await outreach_svc.draft_cold_outreach(
            org_id=self.org_id,
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            contact_title=deal.get("contact_title"),
            industry=deal.get("company_industry") or research.get("industry"),
            icp_score=deal.get("icp_score"),
            icp_rationale=deal.get("icp_rationale"),
            research=research,
        )

        approval_id = await sa_storage.create_deal_approval(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            gate_type="outreach",
            gate_variant="cold",
            draft_subject=draft.get("subject"),
            draft_content=draft.get("email_body"),
        )
        # Mirror onto deal_runs for fast read on the UI.
        await self._set_status(
            "outreach_pending_rep_review",
            extra={
                "outreach_subject": draft.get("subject"),
                "outreach_email_body": draft.get("email_body"),
                "outreach_linkedin_body": draft.get("linkedin_body"),
                "outreach_revision": (deal.get("outreach_revision") or 0) + 1,
            },
        )
        await self.pause_for_approval(approval_id)
        await self.log_step(
            "draft_outreach", "completed", {"approval_id": approval_id}
        )
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="outreach_drafted",
            message="Outreach draft ready for your review.",
            payload={"approval_id": approval_id},
        )
        return {
            "status": "outreach_pending_rep_review",
            "approval_id": approval_id,
        }

    # ── Stage 2 → 3 — after rep approves & sends ──────────────────────────

    async def _step_after_outreach_sent(self) -> dict[str, Any]:
        """The send itself happens in the router (it has the user's Gmail
        creds). Here we just transition into the waiting state and schedule
        the first follow-up."""
        deal = await self._refresh_deal()
        next_due = datetime.now(UTC) + timedelta(days=fu_svc.FOLLOWUP_INTERVAL_DAYS)
        await self._set_status(
            "awaiting_reply",
            extra={"next_followup_due_at": next_due.isoformat()},
            event_message="Outreach sent; awaiting reply.",
        )
        await self.log_step("await_reply", "completed", {"next_followup_due_at": next_due.isoformat()})
        return {"status": "awaiting_reply"}

    # ── Follow-up drafting (called by the cron) ───────────────────────────

    async def step_draft_followup(self) -> dict[str, Any]:
        """Public entry — daily cron calls this when a follow-up is due."""
        deal = await self._refresh_deal()
        if deal.get("status") != "awaiting_reply":
            return {"status": deal.get("status"), "skipped": True}

        idx = (deal.get("followup_count") or 0) + 1
        sent_at = deal.get("outreach_sent_at")
        days_since = _days_since(sent_at)

        draft = await outreach_svc.draft_followup(
            org_id=self.org_id,
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            contact_title=deal.get("contact_title"),
            prior_subject=deal.get("outreach_subject"),
            prior_email_body=deal.get("outreach_email_body"),
            followup_index=idx,
            days_since_outreach=days_since,
        )
        approval_id = await sa_storage.create_deal_approval(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            gate_type="outreach",
            gate_variant=f"followup_{idx}",
            draft_subject=draft.get("subject"),
            draft_content=draft.get("email_body"),
        )
        await self._set_status(
            "followup_pending_rep_review",
            extra={
                "outreach_subject": draft.get("subject"),
                "outreach_email_body": draft.get("email_body"),
            },
        )
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="followup_drafted",
            message=f"Follow-up #{idx} ready for your review.",
            payload={"approval_id": approval_id, "index": idx},
        )
        return {"status": "followup_pending_rep_review", "approval_id": approval_id}

    # ── Check-in drafting (called by the cron, post-proposal) ─────────────

    async def step_draft_checkin(self) -> dict[str, Any]:
        deal = await self._refresh_deal()
        if deal.get("status") != "awaiting_decision":
            return {"status": deal.get("status"), "skipped": True}

        idx = (deal.get("checkin_count") or 0) + 1
        days_since = _days_since(deal.get("proposal_sent_at"))

        draft = await outreach_svc.draft_checkin_nudge(
            org_id=self.org_id,
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            checkin_index=idx,
            days_since_proposal=days_since,
        )
        approval_id = await sa_storage.create_deal_approval(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            gate_type="outreach",
            gate_variant=f"checkin_{idx}",
            draft_subject=draft.get("subject"),
            draft_content=draft.get("email_body"),
        )
        await self._set_status(
            "checkin_pending_rep_review",
            extra={
                "outreach_subject": draft.get("subject"),
                "outreach_email_body": draft.get("email_body"),
            },
        )
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="checkin_drafted",
            message=f"Check-in #{idx} ready for your review.",
            payload={"approval_id": approval_id, "index": idx},
        )
        return {"status": "checkin_pending_rep_review", "approval_id": approval_id}

    # ── Stage 4: meeting prep ─────────────────────────────────────────────

    async def _step_generate_prep_brief(self) -> dict[str, Any]:
        await self.log_step("prep_brief", "started")
        await self._set_status("prep_generating")
        deal = await self._refresh_deal()

        research = await sa_storage.fetch_latest_research(self.deal_run_id) or {}
        brief = await prep_svc.build_meeting_prep_brief(
            org_id=self.org_id,
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            contact_title=deal.get("contact_title"),
            industry=deal.get("company_industry") or research.get("industry"),
            meeting_count=deal.get("meeting_count") or 0,
            research=research,
            prior_call_summary=deal.get("call_summary"),
            prior_bant=deal.get("bant_json"),
            prior_objections=deal.get("objections_json"),
        )
        await self._set_status(
            "prep_ready",
            extra={
                "prep_brief": brief,
                "prep_generated_at": datetime.now(UTC).isoformat(),
            },
        )
        await self.log_step("prep_brief", "completed", {"sources": len(brief.get("sources", []))})
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="prep_brief_ready",
            message="Meeting prep brief ready.",
        )
        return {"status": "prep_ready"}

    # ── Stage 5: call summary (rep submits transcript) ────────────────────

    async def step_process_transcript(self, transcript: str) -> dict[str, Any]:
        await self.log_step("call_summary", "started")
        await self._set_status("call_summarizing")
        deal = await self._refresh_deal()
        research = await sa_storage.fetch_latest_research(self.deal_run_id) or {}

        summary = await cs_svc.extract_call_summary(
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            transcript=transcript,
            research=research,
            prep_brief=deal.get("prep_brief"),
        )
        await self._set_status(
            "call_summarized",
            extra={
                "call_transcript": transcript[:60_000],
                "call_summary": summary.get("summary"),
                "bant_json": summary.get("bant"),
                "objections_json": summary.get("objections"),
                "next_steps_json": summary.get("next_steps"),
                "recommended_stage": summary.get("recommended_stage"),
                "call_summarized_at": datetime.now(UTC).isoformat(),
            },
        )
        await self.log_step("call_summary", "completed", {"recommended": summary.get("recommended_stage")})
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="call_summarized",
            message=summary.get("summary"),
            payload={"recommended_stage": summary.get("recommended_stage")},
        )
        return await self._step_request_next_step_decision()

    async def _step_request_next_step_decision(self) -> dict[str, Any]:
        await self._set_status(
            "awaiting_next_step_decision",
            event_message="Awaiting your decision: propose, another call, or qualify out.",
        )
        return {"status": "awaiting_next_step_decision"}

    # ── Stage 6: proposal generation ──────────────────────────────────────

    async def _step_generate_proposal(self) -> dict[str, Any]:
        await self.log_step("generate_proposal", "started")
        deal = await self._refresh_deal()

        fetched = await sa_storage.fetch_template_docx(
            org_id=self.org_id, template_kind="proposal"
        )
        if not fetched:
            await self.log_step("generate_proposal", "blocked", {"reason": "no_template"})
            return await self._block_missing_template("proposal")

        await self._set_status("proposal_drafting")
        docx_bytes, doc_row = fetched
        research = await sa_storage.fetch_latest_research(self.deal_run_id) or {}

        rendered_context = await proposal_svc.synthesize_proposal_context(
            org_id=self.org_id,
            company_name=deal["company_name"],
            contact_name=deal.get("contact_name"),
            contact_title=deal.get("contact_title"),
            industry=deal.get("company_industry") or research.get("industry"),
            research=research,
            call_summary=deal.get("call_summary"),
            bant=deal.get("bant_json"),
            next_steps=deal.get("next_steps_json"),
            objections=deal.get("objections_json"),
            deal_value_amount=deal.get("deal_value_amount"),
            deal_value_currency=deal.get("deal_value_currency"),
        )
        branding = await self._resolve_org_branding()
        rendered_context.setdefault("our_company_name", branding["name"])
        rendered_context.setdefault("our_company_legal_name", branding["legal_name"])

        try:
            filled_docx, pdf_bytes = await proposal_svc.render_proposal_docx(
                template_bytes=docx_bytes,
                context=rendered_context,
                template_kind="proposal",
            )
        except PdfRenderUnavailable as exc:
            await self.log_step("generate_proposal", "blocked", error=str(exc))
            await self._set_status(
                "blocked_missing_template",
                extra={"blocked_reason": str(exc), "blocked_template_kind": "proposal"},
            )
            return {"status": "blocked_missing_template", "error": "pdf_render_unavailable"}
        except TemplateVariableError as exc:
            await self.log_step("generate_proposal", "failed", error=str(exc))
            await self._set_status(
                "blocked_missing_template",
                extra={"blocked_reason": str(exc), "blocked_template_kind": "proposal"},
            )
            return {"status": "blocked_missing_template", "variable": exc.variable_name}
        except PdfRenderError as exc:
            await self.log_step("generate_proposal", "failed", error=str(exc))
            await self._set_status("failed", extra={"blocked_reason": str(exc)})
            raise

        revision = (deal.get("proposal_revision") or 0) + 1
        upload = await sa_storage.upload_deal_artifact(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            kind="proposal",
            revision=revision,
            pdf_bytes=pdf_bytes,
            docx_bytes=filled_docx,
        )
        doc_id = await sa_storage.insert_deal_document(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            kind="proposal",
            revision=revision,
            source="agent_generated",
            storage_path=upload["docx_path"] or upload["pdf_path"],
            pdf_storage_path=upload["pdf_path"],
            source_template_id=doc_row.get("id"),
            render_context=rendered_context,
            file_bytes=upload["file_bytes"],
        )
        approval_id = await sa_storage.create_deal_approval(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            gate_type="proposal",
            gate_variant="proposal",
            draft_document_id=doc_id,
        )
        await self._set_status(
            "proposal_pending_rep_review",
            extra={
                "proposal_draft_path": upload["docx_path"],
                "proposal_draft_pdf_path": upload["pdf_path"],
                "proposal_revision": revision,
            },
        )
        await self.pause_for_approval(approval_id)
        await self.log_step(
            "generate_proposal",
            "completed",
            {"approval_id": approval_id, "document_id": doc_id, "revision": revision},
        )
        await sa_storage.log_deal_event(
            org_id=self.org_id,
            deal_run_id=self.deal_run_id,
            actor_kind="agent",
            event_type="proposal_drafted",
            message=f"Proposal v{revision} ready for your review.",
            payload={"approval_id": approval_id, "document_id": doc_id},
        )
        return {
            "status": "proposal_pending_rep_review",
            "approval_id": approval_id,
            "document_id": doc_id,
        }

    async def _step_after_proposal_sent(self) -> dict[str, Any]:
        next_due = datetime.now(UTC) + timedelta(days=fu_svc.CHECKIN_INTERVAL_DAYS)
        await self._set_status(
            "awaiting_decision",
            extra={"next_checkin_due_at": next_due.isoformat()},
            event_message="Proposal sent; awaiting decision.",
        )
        return {"status": "awaiting_decision"}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _days_since(iso: str | None) -> int:
    if not iso:
        return 0
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    delta = datetime.now(UTC) - ts
    return max(0, delta.days)
