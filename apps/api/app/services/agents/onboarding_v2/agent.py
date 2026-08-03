"""OnboardingV2Agent — autonomous pre-join HR pipeline.

Triggered when HR clicks "Mark Hired & Start Onboarding" on a recruiting
candidate. What the pipeline *is* comes from the org's step catalog
(`onboarding_step_defs`), snapshotted onto the run at creation time as
`onboarding_run_steps`. There is no fixed sequence in this file: `run()` walks
the run's steps in position order and advances the first one that still needs
something.

Steps come in three kinds.

  generate  Render a document through the 099 generation pipeline, show the
            draft to HR, route it to whoever `signer_roles` names, then send it
            to the candidate. Steps sharing a `bundle_key` are generated,
            approved and signed as one unit.

  collect   Ask the candidate to upload a checklist of documents, then hold the
            step while HR opens each one.

  system    Behaviour that lives here rather than in a template: `bgv` (ask the
            candidate for referees, then email each one a verification form)
            and `policies` (assign acknowledgements and wait). Dispatched on
            `system_action`, not `step_key`, so an org may rename or duplicate
            them.

Per-step state lives on the step row. `onboarding_runs.status` is a coarse
label derived from it — built-in steps keep writing their historical values
(`loi_pending_hr_review`, `bgv_pending`, …) so the dashboards built against
that vocabulary go on working, and org-composed steps write generic ones.

Wherever the candidate acts, the step then parks in `pending_hr_approval` and
waits for HR to accept what they did — a signed document, a finished upload
checklist, a list of referees. Accepting advances the run; rejecting sends the
same ask back to the candidate at the same step, so nothing downstream has to
be unwound. `requires_hr_approval` on the step turns the gate off for an org
that would rather move faster than check.

Every wait is a park, not an error: HR reviewing a draft or a signature, a
candidate uploading, a referee replying. Inngest re-kicks the run when they
act, `run()` finds the same step and moves it on.

This replaced a twenty-branch if/elif over `onboarding_runs.status` that could
only express the one sequence it was written for — LOI → BGV → appointment
letter + NDA → policies → induction, with four booleans to skip parts of it.
That default is now just the catalog every org is seeded with.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger
from app.services.agents.base_agent import BaseAgent
from app.services.agents.onboarding_v2 import catalog as ob_catalog
from app.services.agents.onboarding_v2 import storage as ob_storage
from app.services.agents.onboarding_v2.pre_join import ensure_pre_join_user
from app.services.documents import templates as doc_templates
from app.services.documents.generation import service as doc_generation
from app.services.email import send_email_event
from app.services.notifications import create_notification

log = get_logger(__name__)


# The agent's document kinds against the `document_types.key` values seeded by
# migration 099. Only `loi` differs: the pipeline names the type after the
# document ("letter_of_intent") rather than the agent's internal shorthand.
_DOCUMENT_TYPE_KEY_FOR_KIND = {
    "loi": "letter_of_intent",
    "appointment_letter": "appointment_letter",
    "nda": "nda",
    "induction": "induction",
}

# How many steps one invocation may walk through. A pipeline advances several
# steps in a single kick whenever the ones in between finish instantly — a
# disabled step, a policy step for an org with no policies — so returning after
# the first transition would need one Inngest kick per step. The ceiling is far
# above any real catalog; reaching it means a step is failing to change state,
# and the run parks rather than spinning.
MAX_STEP_TRANSITIONS_PER_RUN = 50


def _format_ctc(run: dict[str, Any]) -> str:
    """CTC as it reads in a notification email.

    Formatted from the run rather than pulled out of a document's render
    context: template variables are named by whoever authored the template, so
    there is no longer a guaranteed `ctc` key to read.
    """
    amount = run.get("ctc_amount")
    if amount in (None, ""):
        return "TBD"
    currency = run.get("ctc_currency") or "INR"
    try:
        return f"{currency} {float(amount):,.2f}"
    except (TypeError, ValueError):
        return str(amount)


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

    async def _set_step(
        self,
        step: dict[str, Any],
        step_status: str,
        *,
        extra: dict[str, Any] | None = None,
        siblings: list[dict[str, Any]] | None = None,
        blocked_reason: str | None = None,
    ) -> None:
        """Move a step (and its bundle) to a state, and relabel the run.

        The step rows are authoritative — `onboarding_runs.status` is a label
        for list views, derived here so the two can never be set independently
        and drift. Built-in steps keep writing their historical status values,
        which is what lets the existing dashboards go on working while the
        pipeline underneath them became configurable.
        """
        for target in siblings or [step]:
            await ob_catalog.set_step_status(
                target["id"], step_status, blocked_reason=blocked_reason
            )
        step["status"] = step_status
        await self._set_status(
            ob_catalog.run_status_for(step, step_status),
            extra={**(extra or {}), "active_step_key": step["step_key"]},
        )

    async def _block_missing_template(
        self,
        template_kind: str,
        reason: str | None = None,
        *,
        step: dict[str, Any] | None = None,
        siblings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Park the run in blocked_missing_template; HR will see a clear
        prompt to fix the template and the agent will be re-kicked once it is.

        `reason` carries the generation service's own words when it has them —
        "no fields have been confirmed on 'LOI'" is a different
        problem from a missing template, and telling HR to upload one they can
        see is already there sends them looking in the wrong place.
        """
        detail = reason or (
            f"No {template_kind.replace('_', ' ')} template is set as the default. "
            "Upload one in Document templates and mark it as the default."
        )
        extra = {
            "blocked_reason": detail,
            "blocked_template_kind": template_kind,
        }
        if step is not None:
            await self._set_step(
                step,
                ob_catalog.STATUS_BLOCKED_MISSING_TEMPLATE,
                siblings=siblings,
                blocked_reason=detail,
                extra=extra,
            )
        else:
            await self._set_status("blocked_missing_template", extra=extra)
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="blocked_missing_template",
            message=detail,
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

    # ── Rendering ─────────────────────────────────────────────────────────

    def _type_key(self, kind: str) -> str:
        """The `document_types.key` behind a step key.

        A fallback for callers that only have the key. The step row carries
        `document_type_key` and that is authoritative — step keys are unique
        per org, so a second step rendering the same template gets a suffixed
        key (`nda_2`) that is not a document type at all. Prefer
        `_step_type_key`.
        """
        return _DOCUMENT_TYPE_KEY_FOR_KIND.get(kind, kind)

    def _step_type_key(self, step: dict[str, Any]) -> str:
        """Which template this step renders."""
        return step.get("document_type_key") or self._type_key(step["step_key"])

    async def _template_is_ready(self, type_key: str) -> bool:
        """Is there an active default template for this document type?

        A cheap pre-check so the caller can bail BEFORE flipping the run to a
        `*_generating` status. Without it, a missing template (or a crash
        mid-generation) leaves the run stuck under a misleading "Generating…"
        label — the same reason the previous implementation fetched the
        template first.
        """
        resolved = await doc_templates.resolve_default_version(
            org_id=self.org_id, type_key=type_key
        )
        return resolved is not None

    async def _warn_generation(self, *, kind: str, outcome: Any) -> None:
        """Surface non-fatal generation warnings on the run timeline.

        Covers a PDF conversion that failed and unfilled spots the renderer
        found in the output. Strictly informational — a document with one
        unfilled line is still more useful to HR than a failed run.
        """
        if not outcome.warnings:
            return
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="document_generated_with_warnings",
            message=f"{kind}: {outcome.warnings[0]}",
            metadata={"kind": kind, "warnings": outcome.warnings},
        )

    async def _generate_document(self, type_key: str) -> Any:
        """Produce one document through the document generation pipeline.

        The agent's single seam into `services/documents`. Everything the old
        path did piecemeal — find the template, resolve the candidate, map
        fields, validate, render, record — happens inside one call that returns
        an outcome rather than raising, so the three generation steps branch on
        data instead of maintaining near-identical except-ladders.

        The pipeline writes the canonical `generated_documents` /
        `generated_files` rows itself. The caller still uploads the bytes to the
        run's own artifact path and upserts `onboarding_documents`, because the
        run timeline, the HR review screen, and the e-sign handoff all read from
        there. Two records of one document, deliberately: one is the versioned
        audit trail, the other is this run's working copy.

        `field_values` carries whatever HR typed into the blocked-run form after
        a previous attempt failed validation. It has to be re-read on every
        attempt rather than captured once, because filling that form is exactly
        what re-kicks the agent.
        """
        run = await self._refresh_run()
        return await doc_generation.generate(
            org_id=self.org_id,
            type_key=type_key,
            onboarding_run_id=self.onboarding_run_id,
            variable_values=run.get("field_values") or {},
        )

    async def _handle_generation_failure(
        self,
        *,
        kind: str,
        log_step: str,
        outcome: Any,
        run_step: dict[str, Any],
        siblings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Turn a non-ok generation outcome into the right step state.

        Returns the step's return value, or None when the outcome was fine and
        the caller should carry on. Centralised because every generation step
        needs identical handling and previously had three drifting copies of it.

        A failure blocks the step, not the run: the retry re-enters at exactly
        this step rather than restarting the pipeline, and a bundle blocks as a
        unit because generating half of it is not a state anyone can act on.
        """
        if outcome.ok:
            return None

        error = outcome.error or "Document generation failed."

        if outcome.outcome in (
            doc_generation.OUTCOME_MISSING_TEMPLATE,
            doc_generation.OUTCOME_NO_FIELDS,
        ):
            await self.log_step(
                log_step, "skipped", {"reason": outcome.outcome, "kind": kind}
            )
            return await self._block_missing_template(
                kind, reason=error, step=run_step, siblings=siblings
            )

        if outcome.outcome == doc_generation.OUTCOME_DRIFT:
            await self.log_step(log_step, "blocked", error=f"{kind}: {error}")
            return await self._block_template_drift(
                kind, RuntimeError(error), step=run_step, siblings=siblings
            )

        if outcome.outcome == doc_generation.OUTCOME_VALIDATION_FAILED:
            # Missing or invalid candidate data. Actionable by HR, so it is a
            # blocked state with the specific fields named — never a document
            # with a gap where the manager's name should be.
            await self.log_step(log_step, "blocked", error=f"{kind}: {error}")
            await self._set_step(
                run_step,
                ob_catalog.STATUS_BLOCKED_MISSING_TEMPLATE,
                siblings=siblings,
                blocked_reason=error,
                extra={"blocked_reason": error, "blocked_template_kind": kind},
            )
            await ob_storage.log_onboarding_event(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                actor_kind="agent",
                event_type="document_validation_failed",
                message=error,
                metadata={
                    "kind": kind,
                    "document_id": outcome.generated_document_id,
                    "report": outcome.validation_report,
                },
            )
            return {"status": "blocked_missing_template", "reason": "validation_failed"}

        # Render or storage failure — retryable. Rewind the step to pending so
        # the next kick re-runs it from the top, rather than leaving it parked
        # under a "Generating…" label nothing will ever move off.
        await self.log_step(log_step, "blocked", error=f"{kind}: {error}")
        await self._set_step(
            run_step,
            ob_catalog.STATUS_PENDING,
            siblings=siblings,
            extra={"blocked_reason": error},
        )
        return {"status": "generation_unavailable", "error": outcome.outcome}

    async def _block_template_drift(
        self,
        template_kind: str,
        exc: Exception,
        *,
        step: dict[str, Any] | None = None,
        siblings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Park the run because the template changed after its fields were
        confirmed.

        Distinct from `_block_missing_template`: the template is present and
        active, but we can no longer vouch for where its values go, so we refuse
        to write rather than risk splicing a salary into the middle of a clause.
        Recovery is HR re-confirming in the mapper, which fires
        `onboarding_v2/template_uploaded` and re-drives the run.
        """
        extra = {
            "blocked_reason": str(exc),
            "blocked_template_kind": template_kind,
        }
        if step is not None:
            await self._set_step(
                step,
                ob_catalog.STATUS_BLOCKED_TEMPLATE_DRIFT,
                siblings=siblings,
                blocked_reason=str(exc),
                extra=extra,
            )
        else:
            await self._set_status("blocked_template_drift", extra=extra)
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="blocked_template_drift",
            message=str(exc),
            metadata={
                "template_kind": template_kind,
                "paragraph_indexes": exc.paragraph_indexes[:10],
            },
        )
        return {"status": "blocked_template_drift", "template_kind": template_kind}

    # ── Main dispatcher ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Re-entrant: walk the run's steps and advance the first unfinished one.

        The pipeline is whatever the org composed — `onboarding_run_steps`, in
        position order, snapshotted from the catalog when the run started. This
        replaced a twenty-branch if/elif over `onboarding_runs.status`, which
        could only ever express the one sequence it was written for.

        Steps that wait on a human (HR reviewing a draft, a candidate uploading
        documents, a referee replying) return without erroring; the run is
        re-kicked when they act. Steps that finish instantly — a disabled one, a
        policy step with no policies — hand straight to the next, which is why
        this loops rather than returning after one transition.
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

        steps = await ob_catalog.materialize_run_steps(
            org_id=self.org_id, run_id=self.onboarding_run_id
        )
        if not steps:
            # An org with no catalog at all. Parking beats completing: a run
            # that reports "done" without having done anything is worse than
            # one visibly waiting for someone to configure the pipeline.
            await self.log_step("dispatch", "skipped", {"reason": "no_steps"})
            return {"status": current, "no_steps": True}

        # Bounded so a bug that leaves a step in the state it started in costs
        # one invocation rather than spinning. The ceiling is well clear of any
        # real pipeline; hitting it means something is wrong, and the run parks
        # where the next kick can retry.
        for _ in range(MAX_STEP_TRANSITIONS_PER_RUN):
            step = ob_catalog.next_actionable(steps)
            if step is None:
                return await self._step_finalise()

            result = await self._advance(steps, step)
            if not result.pop("_continue", False):
                return result
            steps = await ob_catalog.get_run_steps(self.onboarding_run_id)

        await self.log_step("dispatch", "blocked", error="step_transition_limit")
        return {"status": (await self._refresh_run()).get("status"), "throttled": True}

    async def _advance(
        self, steps: list[dict[str, Any]], step: dict[str, Any]
    ) -> dict[str, Any]:
        """Move one step forward. `_continue` asks run() for another lap."""
        kind = step.get("kind")
        if kind == ob_catalog.KIND_COLLECT:
            return await self._advance_collect(step)
        if kind == ob_catalog.KIND_SYSTEM:
            return await self._advance_system(step)
        return await self._advance_generate(steps, step)

    # ── The HR approval gate ──────────────────────────────────────────────

    async def _park_for_approval(
        self,
        step: dict[str, Any],
        *,
        detail: str,
        siblings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Hold the step until HR accepts or rejects what the candidate did.

        Idempotent: a step already sitting in the gate is left alone. Every
        unrelated kick — a second run event, a cron sweep — walks the pipeline
        and arrives here again, and re-announcing would mail HR the same review
        request once a day until they opened it.

        Nothing here decides what happens next. HR answers through
        `/steps/{key}/review`, which either advances the step or sends the ask
        back to the candidate.
        """
        if step.get("status") == ob_catalog.STATUS_PENDING_HR_APPROVAL:
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "hr_to_approve_candidate_work",
                "step_key": step["step_key"],
            }

        await self._set_step(
            step, ob_catalog.STATUS_PENDING_HR_APPROVAL, siblings=siblings
        )
        await self.log_step(
            "approval", "waiting", {"step_key": step["step_key"]}
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="step_pending_hr_approval",
            message=f"{detail} Review it and either accept it or send it back.",
            metadata={
                "step_key": step["step_key"],
                "round": step.get("approval_round") or 0,
            },
        )
        await self._notify_hr_approval_needed(step, detail=detail)
        return {
            "status": (await self._load_run()).get("status"),
            "waiting_for": "hr_to_approve_candidate_work",
            "step_key": step["step_key"],
        }

    async def _notify_hr_approval_needed(
        self, step: dict[str, Any], *, detail: str
    ) -> None:
        """Email the HR user who started the run that something needs checking.

        Dedupe is keyed by round, not just by step: HR sending a document back
        and the candidate re-signing it produces a second, genuinely different
        review request, and a step-only key would suppress it as a replay of
        the first.
        """
        run = await self._load_run()
        hr_email = await self._hr_email()
        if not hr_email:
            await self.log_step("notify_hr", "skipped", {"reason": "no_hr_email"})
            return

        settings = get_settings()
        label = step.get("bundle_label") or step.get("label") or step["step_key"]
        try:
            await send_email_event(
                event_type="onboarding_step_approval_needed",
                to=hr_email,
                user_id=run.get("triggered_by_user_id"),
                org_id=self.org_id,
                dedupe_key=(
                    f"approval-{self.onboarding_run_id}-{step['step_key']}-"
                    f"{step.get('approval_round') or 0}"
                ),
                data={
                    "candidate_name": run["candidate_name"],
                    "role_title": run["role_title"],
                    "step_label": label,
                    "summary": detail,
                    "run_url": (
                        f"{settings.app_url.rstrip('/')}/onboarding/"
                        f"{self.onboarding_run_id}"
                        if settings.app_url
                        else None
                    ),
                    "app_url": (
                        settings.app_url.rstrip("/") if settings.app_url else None
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.approval_email_failed err=%s", exc)

    async def _hr_email(self) -> str | None:
        """The address of the HR user who started this run, if resolvable."""
        run = await self._load_run()
        triggered_by = run.get("triggered_by_user_id")
        if not triggered_by:
            return None
        svc = get_service_client()
        try:
            au = await asyncio.to_thread(
                lambda: svc.auth.admin.get_user_by_id(triggered_by)
            )
            return getattr(getattr(au, "user", None), "email", None)
        except Exception:  # noqa: BLE001
            return None

    # ── Collect steps ─────────────────────────────────────────────────────

    async def _advance_collect(self, step: dict[str, Any]) -> dict[str, Any]:
        """Ask the candidate for documents, and wait until they are all in.

        Everything in does not mean the step is finished. Unless the org turned
        the gate off, the checklist then parks in `pending_hr_approval` so HR
        can open each file before the run moves on — a marksheet photographed
        at an angle nobody can read is worth catching here rather than two
        steps later, when asking again means unwinding the pipeline.

        A file HR sent back stops counting as filed, which is what re-opens the
        ask: `outstanding` picks it up again on the next kick exactly as if it
        had never been uploaded.
        """
        submissions = await ob_catalog.get_submissions(self.onboarding_run_id)
        outstanding = [
            i["item_key"]
            for i in ob_catalog.required_items(step)
            if i["item_key"]
            not in {
                s["item_key"]
                for s in submissions
                if s.get("run_step_id") == step["id"]
                and s.get("review_status") != ob_catalog.REVIEW_REJECTED
            }
        ]

        if not outstanding:
            if not ob_catalog.gate_cleared(step):
                return await self._park_for_approval(
                    step,
                    detail=(
                        f"{step.get('label') or step['step_key']}: "
                        f"{len(ob_catalog.required_items(step))} document(s) "
                        "received from the candidate."
                    ),
                )
            await self._set_step(step, ob_catalog.STATUS_DONE)
            await self.log_step(
                "collect", "completed", {"step_key": step["step_key"]}
            )
            return {"_continue": True}

        # First time the run reaches this step, put it in front of the
        # candidate. Later kicks (a partial upload, an unrelated event) find it
        # already active and stay quiet rather than re-emailing.
        if step.get("status") != ob_catalog.STATUS_ACTIVE:
            await self._set_step(step, ob_catalog.STATUS_ACTIVE)
            await self._notify_candidate_documents_due(step)

        await self.log_step(
            "collect",
            "waiting",
            {"step_key": step["step_key"], "outstanding": outstanding},
        )
        return {
            "status": (await self._load_run()).get("status"),
            "waiting_for": "candidate_documents",
            "step_key": step["step_key"],
            "outstanding": outstanding,
        }

    # ── System steps ──────────────────────────────────────────────────────

    async def _advance_system(self, step: dict[str, Any]) -> dict[str, Any]:
        """Background verification and policy acknowledgement.

        Dispatched on `system_action` rather than `step_key` so an org can
        rename the step, or run two of them, without changing what it does.
        """
        action = step.get("system_action")
        if action == ob_catalog.SYSTEM_ACTION_BGV:
            return await self._advance_bgv(step)
        if action == ob_catalog.SYSTEM_ACTION_POLICIES:
            return await self._advance_policies(step)

        # A system step with no action does nothing knowable. Skipping beats
        # parking the run on it forever.
        await self.log_step(
            "system", "skipped", {"step_key": step["step_key"], "reason": "no_action"}
        )
        await self._set_step(step, ob_catalog.STATUS_SKIPPED)
        return {"_continue": True}

    async def _advance_bgv(self, step: dict[str, Any]) -> dict[str, Any]:
        """Ask the candidate for references, then each referee for a reply.

        Four waits in one step: for the candidate to name their referees, for
        HR to accept that list, for the verification emails to go out, and for
        the referees to answer. The references form token is minted here rather
        than inherited from the LOI email, which is what lets background
        verification run first, or without an LOI at all.

        HR's look comes before the emails, not after, and that ordering is the
        whole value of it. A referee who is the candidate's flatmate, or a
        personal address where a manager was asked for, can be sent back and
        replaced; once the verification form has gone out, it has gone out.
        """
        run = await self._refresh_run()

        if not run.get("references_form_token"):
            await self._request_candidate_references(step)
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "candidate_to_submit_references",
                "step_key": step["step_key"],
            }

        if not run.get("references_submitted_at"):
            if step.get("status") != ob_catalog.STATUS_ACTIVE:
                await self._set_step(step, ob_catalog.STATUS_ACTIVE)
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "candidate_to_submit_references",
                "step_key": step["step_key"],
            }

        svc = get_service_client()
        refs = await asyncio.to_thread(
            lambda: svc.table("onboarding_bgv_references")
            .select("id, token, reference_email, reference_name, status")
            .eq("run_id", self.onboarding_run_id)
            .execute()
        )
        # A referee HR rejected is kept for the timeline and ignored here, so a
        # superseded list neither holds the step open nor gets re-mailed.
        rows: list[dict[str, Any]] = [
            r for r in (refs.data or []) if r.get("status") != "superseded"
        ]

        if rows and not ob_catalog.gate_cleared(step):
            return await self._park_for_approval(
                step,
                detail=(
                    f"{run['candidate_name']} named {len(rows)} "
                    f"reference{'' if len(rows) == 1 else 's'}. Nothing has been "
                    "emailed to them yet."
                ),
            )

        if not rows:
            # The candidate submitted the form without naming anyone, or HR
            # overrode it empty. Nothing to verify, and blocking here would
            # strand the hire on a step no one can action.
            await self.log_step(
                "bgv", "skipped", {"reason": "no_references_entered"}
            )
            await self._set_step(step, ob_catalog.STATUS_DONE)
            return {"_continue": True}

        unsent = [r for r in rows if r.get("status") in (None, "pending")]
        if unsent:
            await self._email_bgv_references(unsent)
            await self._set_step(step, ob_catalog.STATUS_ACTIVE)
            rows = [
                {**r, "status": "sent"} if r in unsent else r for r in rows
            ]

        if any(r.get("status") != "submitted" for r in rows):
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "bgv_responses",
                "step_key": step["step_key"],
            }

        await self._set_step(
            step,
            ob_catalog.STATUS_DONE,
            extra={"bgv_completed_at": datetime.now(UTC).isoformat()},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="bgv_complete",
            message="All references have responded.",
        )
        return {"_continue": True}

    async def _request_candidate_references(self, step: dict[str, Any]) -> None:
        """Mint the references form token and email the candidate its link."""
        from datetime import timedelta
        from uuid import uuid4

        run = await self._load_run()
        settings = get_settings()
        token = str(uuid4())
        # 14 days matches the referee-token convention so the cron reminder
        # window has room to fire before it lapses.
        expires_at = (datetime.now(UTC) + timedelta(days=14)).isoformat()
        form_url = (
            f"{settings.app_url.rstrip('/')}/references/{token}"
            if settings.app_url
            else None
        )

        await self._set_step(
            step,
            ob_catalog.STATUS_ACTIVE,
            extra={
                "references_form_token": token,
                "references_form_expires_at": expires_at,
            },
        )

        try:
            await send_email_event(
                event_type="onboarding_references_requested",
                to=run["candidate_email"],
                user_id=run.get("pre_join_user_id"),
                org_id=self.org_id,
                dedupe_key=f"refs-{self.onboarding_run_id}",
                data={
                    "candidate_name": run["candidate_name"],
                    "role_title": run["role_title"],
                    "company_name": (await self._resolve_org_branding())["name"],
                    "references_form_url": form_url,
                    "app_url": settings.app_url.rstrip("/") if settings.app_url else None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.references_email_failed err=%s", exc)

        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="candidate_references_requested",
            message=f"Asked {run['candidate_name']} to submit BGV references.",
            metadata={"step_key": step["step_key"]},
        )

    async def _email_bgv_references(self, refs: list[dict[str, Any]]) -> int:
        """Send each referee their verification form. Returns how many went."""
        run = await self._load_run()
        settings = get_settings()
        base_url = (
            settings.bgv_public_url.rstrip("/")
            if settings.bgv_public_url
            else settings.app_url.rstrip("/")
        )
        svc = get_service_client()
        company = await self._resolve_org_name()

        sent = 0
        for ref in refs:
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
                        "company_name": company,
                        "role_title": run["role_title"],
                        "form_url": f"{base_url}/bgv/{ref['token']}",
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
                sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "onboarding_v2.bgv_email_failed ref=%s err=%s", ref["id"], exc
                )

        await self.log_step("kick_off_bgv", "completed", {"sent_count": sent})
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="bgv_emails_sent",
            message=f"Verification emails sent to {sent} reference(s).",
            metadata={"sent_count": sent},
        )
        return sent

    async def _advance_policies(self, step: dict[str, Any]) -> dict[str, Any]:
        """Assign policy acknowledgements, then wait for the candidate."""
        run = await self._refresh_run()
        user_id = run.get("pre_join_user_id")
        if not user_id:
            # Provisioned at run creation; retry rather than fail, since a
            # transient auth error at creation time should not cost the hire.
            try:
                user_id = await ensure_pre_join_user(
                    org_id=self.org_id,
                    run_id=self.onboarding_run_id,
                    candidate_email=run["candidate_email"],
                    candidate_name=run["candidate_name"],
                )
            except Exception as exc:  # noqa: BLE001
                await self.log_step(
                    "assign_policies",
                    "failed",
                    error=f"pre_join_provisioning_failed: {exc}",
                )
                await self._set_step(
                    step,
                    ob_catalog.STATUS_FAILED,
                    blocked_reason=f"pre_join_provisioning_failed: {exc}",
                )
                await self._set_status(
                    "failed",
                    extra={"blocked_reason": f"pre_join_provisioning_failed: {exc}"},
                )
                raise

        if step.get("status") != ob_catalog.STATUS_ACTIVE:
            assigned = await self._assign_policy_acks(user_id)
            if assigned is None:
                await self._set_step(step, ob_catalog.STATUS_DONE)
                return {"_continue": True}
            await self._set_step(
                step,
                ob_catalog.STATUS_ACTIVE,
                extra={"policies_assigned_at": datetime.now(UTC).isoformat()},
            )
            await self._notify_candidate_policies(user_id, assigned)
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "policy_acknowledgements",
                "step_key": step["step_key"],
            }

        svc = get_service_client()
        pending = await asyncio.to_thread(
            lambda: svc.table("acknowledgements")
            .select("id", count="exact")
            .eq("org_id", self.org_id)
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        if (pending.count or 0) > 0:
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "policy_acknowledgements",
                "pending": pending.count,
            }

        await self._set_step(
            step,
            ob_catalog.STATUS_DONE,
            extra={"policies_acknowledged_at": datetime.now(UTC).isoformat()},
        )
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="policies_acknowledged",
            message="All policy acknowledgements complete.",
        )
        return {"_continue": True}

    async def _assign_policy_acks(self, user_id: str) -> int | None:
        """Create acknowledgement rows for every policy doc. None = no policies.

        Reuses the compliance system from migration 031, so the existing
        acknowledgement UI works on these without knowing they came from a hire.
        """
        svc = get_service_client()
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
            return None

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
                    lambda p=payload: svc.table("acknowledgements").insert(p).execute()
                )
                inserted += 1
            except Exception as exc:
                # Duplicate (already assigned) — skip silently.
                if "duplicate" not in str(exc).lower():
                    log.warning(
                        "onboarding_v2.ack_insert_failed doc=%s err=%s", d["id"], exc
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
        return inserted

    async def _notify_candidate_policies(self, user_id: str, count: int) -> None:
        run = await self._load_run()
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
                    "policy_count": count,
                    "app_url": settings.app_url.rstrip("/"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.policy_email_failed err=%s", exc)

    async def _ensure_documents_token(self) -> str:
        """The candidate's upload link, minted on the first ask and kept alive.

        One token per run, not per step: a run may ask for documents at two
        points, and a candidate who bookmarked the first link should find the
        second batch there rather than needing a new URL. Re-issuing on every
        ask would also invalidate a link the candidate is mid-way through.

        The expiry is pushed forward on each ask instead. 30 days rather than
        the 14 the reference forms use — joining paperwork gets chased across a
        notice period, and a candidate coming back in week three should not
        find a dead link and have to email HR for another one.
        """
        from datetime import timedelta
        from uuid import uuid4

        run = await self._load_run()
        token = run.get("documents_token") or str(uuid4())
        expires_at = (datetime.now(UTC) + timedelta(days=30)).isoformat()

        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("onboarding_runs")
            .update(
                {
                    "documents_token": token,
                    "documents_token_expires_at": expires_at,
                }
            )
            .eq("id", self.onboarding_run_id)
            .execute()
        )
        if self._run_row is not None:
            self._run_row["documents_token"] = token
            self._run_row["documents_token_expires_at"] = expires_at
        return token

    async def _notify_candidate_documents_due(self, step: dict[str, Any]) -> None:
        """Ask the candidate for this step's documents.

        The link carries its own credential, so the candidate uploads straight
        from the email without signing in — they are given an account at run
        creation, but the magic-link email that reaches it is weeks old by the
        time the first document is asked for and is the one people lose.
        """
        token = await self._ensure_documents_token()
        run = await self._load_run()
        settings = get_settings()
        items = ob_catalog.required_items(step)
        try:
            await send_email_event(
                event_type="onboarding_documents_requested",
                to=run["candidate_email"],
                user_id=run.get("pre_join_user_id"),
                org_id=self.org_id,
                dedupe_key=f"collect-{self.onboarding_run_id}-{step['step_key']}",
                data={
                    "candidate_name": run["candidate_name"],
                    "company_name": (await self._resolve_org_branding())["name"],
                    "step_label": step.get("label") or "Documents",
                    "document_count": len(items),
                    "document_labels": [i["label"] for i in items],
                    # Not /documents/{token}: that prefix is a protected
                    # dashboard route, so the proxy would bounce the candidate
                    # to /login — the exact thing this link exists to avoid.
                    "portal_url": (
                        f"{settings.app_url.rstrip('/')}/candidate/documents/{token}"
                        if settings.app_url
                        else None
                    ),
                    "app_url": settings.app_url.rstrip("/") if settings.app_url else None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.collect_email_failed err=%s", exc)

        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="candidate_documents_requested",
            message=(
                f"Asked {run['candidate_name']} for {len(items)} document(s): "
                f"{step.get('label')}."
            ),
            metadata={"step_key": step["step_key"]},
        )

    # ── Generate steps ────────────────────────────────────────────────────

    async def _advance_generate(
        self, steps: list[dict[str, Any]], step: dict[str, Any]
    ) -> dict[str, Any]:
        """Render a document (or a bundle of them), get it signed, send it on.

        A bundle moves as one: every member is generated before any is shown to
        HR, one approval covers all of them, and they go into a single signing
        envelope. That was hardcoded for appointment-letter-plus-NDA; it is now
        whatever documents an org grouped together.

        HR sees the documents twice when the candidate signs them: once as a
        draft, before anyone signs, and again afterwards, to check the
        signature actually landed where it was meant to. The second look is the
        approval gate, and it is the only thing between a signed PDF and the
        copy the candidate keeps.
        """
        bundle = ob_catalog.bundle_siblings(steps, step)
        status = step.get("status")

        if status in (
            ob_catalog.STATUS_PENDING,
            ob_catalog.STATUS_GENERATING,
            ob_catalog.STATUS_BLOCKED_MISSING_TEMPLATE,
            ob_catalog.STATUS_BLOCKED_TEMPLATE_DRIFT,
        ):
            return await self._generate_bundle(bundle)

        if status == ob_catalog.STATUS_PENDING_HR_REVIEW:
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "hr_to_approve_draft",
                "step_key": step["step_key"],
            }

        if status == ob_catalog.STATUS_PENDING_SIGNATURE:
            if await self._signatures_complete(bundle):
                if not ob_catalog.gate_cleared(step):
                    return await self._park_for_approval(
                        step,
                        siblings=bundle,
                        detail=(
                            f"{step.get('bundle_label') or step.get('label')} "
                            "has been signed and is ready for you to check."
                        ),
                    )
                return await self._deliver_bundle(bundle)
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "signatures",
                "step_key": step["step_key"],
            }

        if status == ob_catalog.STATUS_PENDING_HR_APPROVAL:
            return {
                "status": (await self._load_run()).get("status"),
                "waiting_for": "hr_to_approve_candidate_work",
                "step_key": step["step_key"],
            }

        if status == ob_catalog.STATUS_ACTIVE:
            # Signed and checked, or approved with nobody to sign. Either way
            # the documents are final and the candidate should have them.
            return await self._deliver_bundle(bundle)

        await self.log_step(
            "generate", "skipped", {"step_key": step["step_key"], "status": status}
        )
        return {"status": (await self._load_run()).get("status"), "no_op": True}

    async def _generate_bundle(self, bundle: list[dict[str, Any]]) -> dict[str, Any]:
        """Render every document in the bundle and hand them to HR."""
        lead = bundle[0]
        label = lead.get("bundle_label") or lead.get("label") or lead["step_key"]
        await self.log_step("generate", "started", {"step_key": lead["step_key"]})

        # Check every template BEFORE flipping to `generating` — otherwise a
        # missing one leaves the run stuck under a misleading "Generating…"
        # label with nothing coming.
        for member in bundle:
            if not await self._template_is_ready(self._step_type_key(member)):
                await self.log_step(
                    "generate",
                    "skipped",
                    {"reason": f"no_template:{member['step_key']}"},
                )
                # Blocked on the *type*, because that is what HR uploads and
                # what `onboarding_v2/template_uploaded` fans out on.
                return await self._block_missing_template(
                    self._step_type_key(member), step=lead, siblings=bundle
                )

        await self._set_step(lead, ob_catalog.STATUS_GENERATING, siblings=bundle)

        for member in bundle:
            kind = member["step_key"]
            outcome = await self._generate_document(self._step_type_key(member))
            handled = await self._handle_generation_failure(
                kind=kind,
                log_step="generate",
                outcome=outcome,
                run_step=lead,
                siblings=bundle,
            )
            if handled is not None:
                return handled

            storage = await ob_storage.upload_onboarding_artifact(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                kind=kind,
                pdf_bytes=outcome.pdf_bytes or b"",
                docx_bytes=outcome.docx_bytes,
            )
            await ob_storage.upsert_onboarding_document(
                org_id=self.org_id,
                run_id=self.onboarding_run_id,
                kind=kind,
                storage=storage,
                source_template_id=outcome.template_id,
                render_context=outcome.context,
                sign_status="draft",
                run_step_id=member["id"],
            )
            await self._warn_generation(kind=kind, outcome=outcome)

        await self._set_step(lead, ob_catalog.STATUS_PENDING_HR_REVIEW, siblings=bundle)
        await self.log_step("generate", "completed", {"step_key": lead["step_key"]})
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="documents_ready_for_review",
            message=(
                f"{label} ready for HR review. Preview on the run page, edit if "
                "needed, then approve to send it on."
            ),
            metadata={"step_key": lead["step_key"], "documents": len(bundle)},
        )
        await self._notify_hr_documents_ready(bundle)
        return {
            "status": (await self._load_run()).get("status"),
            "waiting_for": "hr_to_approve_draft",
            "step_key": lead["step_key"],
        }

    async def _signatures_complete(self, bundle: list[dict[str, Any]]) -> bool:
        """Has every signer finished with this bundle's documents?

        apps/esign stamps `onboarding_documents` when the last signer completes
        and does not touch the run, so the document rows are the only place
        that knows. A manually-uploaded scan sets `signed_pdf_path` instead.
        """
        docs = await self._bundle_documents(bundle)
        if not docs:
            return False
        return all(
            d.get("esign_status") == "completed"
            or d.get("sign_status") == "signed_by_candidate"
            or d.get("signed_pdf_path")
            for d in docs
        )

    async def _bundle_documents(
        self, bundle: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """This bundle's `onboarding_documents` rows, in bundle order."""
        svc = get_service_client()
        kinds = [m["step_key"] for m in bundle]
        res = await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .select(
                "id, kind, storage_path, signed_pdf_path, hr_edited_pdf_path, "
                "sign_status, esign_status, esign_signing_url"
            )
            .eq("run_id", self.onboarding_run_id)
            .in_("kind", kinds)
            .execute()
        )
        by_kind = {d["kind"]: d for d in (res.data or [])}
        return [by_kind[k] for k in kinds if k in by_kind]

    async def _deliver_bundle(self, bundle: list[dict[str, Any]]) -> dict[str, Any]:
        """Email the finished documents to the candidate and close the step."""
        lead = bundle[0]
        run = await self._load_run()
        settings = get_settings()
        branding = await self._resolve_org_branding()
        docs = await self._bundle_documents(bundle)
        by_key = {m["step_key"]: m for m in bundle}

        svc = get_service_client()

        def _signed_url(path: str) -> str | None:
            try:
                res = svc.storage.from_(ob_storage.STORAGE_BUCKET).create_signed_url(
                    path=path, expires_in=ob_storage.SIGNED_URL_TTL_SECONDS
                )
                return res.get("signedURL") or res.get("signed_url")
            except Exception:
                return None

        links: list[dict[str, Any]] = []
        for doc in docs:
            path = (
                doc.get("signed_pdf_path")
                or doc.get("hr_edited_pdf_path")
                or doc.get("storage_path")
            )
            url = (
                await asyncio.to_thread(lambda p=path: _signed_url(p)) if path else None
            )
            member = by_key.get(doc["kind"], {})
            links.append({"label": member.get("label") or doc["kind"], "url": url})

        event, payload = self._delivery_email(lead, run, branding, links, settings)
        try:
            await send_email_event(
                event_type=event,
                to=run["candidate_email"],
                user_id=run.get("pre_join_user_id"),
                org_id=self.org_id,
                dedupe_key=f"deliver-{self.onboarding_run_id}-{lead['step_key']}",
                data=payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("onboarding_v2.delivery_email_failed err=%s", exc)

        await asyncio.to_thread(
            lambda: svc.table("onboarding_documents")
            .update({"sign_status": "sent_to_candidate"})
            .eq("run_id", self.onboarding_run_id)
            .in_("kind", [m["step_key"] for m in bundle])
            .execute()
        )

        label = lead.get("bundle_label") or lead.get("label") or lead["step_key"]
        await self._set_step(lead, ob_catalog.STATUS_DONE, siblings=bundle)
        await self.log_step("deliver", "completed", {"step_key": lead["step_key"]})
        await ob_storage.log_onboarding_event(
            org_id=self.org_id,
            run_id=self.onboarding_run_id,
            actor_kind="agent",
            event_type="documents_sent_to_candidate",
            message=f"{label} sent to {run['candidate_email']}.",
            metadata={"step_key": lead["step_key"]},
        )
        return {"_continue": True}

    def _delivery_email(
        self,
        lead: dict[str, Any],
        run: dict[str, Any],
        branding: dict[str, Any],
        links: list[dict[str, Any]],
        settings: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Which email announces this step's documents, and what it needs.

        The two built-in documents keep their bespoke copy — the LOI is the
        candidate's first contact and the induction pack is a welcome, and a
        generic "here are your documents" would be a downgrade for both.
        Anything an org composed gets the generic template, which names the step
        and lists what is attached.

        Dispatched on the document type rather than `step_key` for the same
        reason the LOI review panel is: an org that renamed or re-created the
        step renders the same document under a key of its own choosing.
        """
        app_url = settings.app_url.rstrip("/") if settings.app_url else None
        type_key = self._step_type_key(lead)
        first_url = links[0]["url"] if links else None

        if type_key == ob_catalog.DOCUMENT_TYPE_LOI:
            token = run.get("references_form_token")
            return "onboarding_loi_to_candidate", {
                "candidate_name": run["candidate_name"],
                "role_title": run["role_title"],
                "company_name": branding["name"],
                "start_date": str(run["start_date"]),
                "loi_signed_url": first_url,
                # Only when background verification has already asked. If it
                # runs later, or not at all, the template omits the section.
                "references_form_url": (
                    f"{app_url}/references/{token}" if token and app_url else None
                ),
                "app_url": app_url,
            }

        if type_key == ob_catalog.DOCUMENT_TYPE_INDUCTION:
            return "onboarding_induction_ready", {
                "candidate_name": run["candidate_name"],
                "role_title": run["role_title"],
                "company_name": branding["name"],
                "start_date": str(run["start_date"]),
                "induction_signed_url": first_url,
                "app_url": app_url,
            }

        return "onboarding_documents_sent", {
            "candidate_name": run["candidate_name"],
            "role_title": run["role_title"],
            "company_name": branding["name"],
            "step_label": (
                lead.get("bundle_label") or lead.get("label") or lead["step_key"]
            ),
            "documents": links,
            "signing_url": None,
            "app_url": app_url,
        }

    async def _notify_hr_documents_ready(self, bundle: list[dict[str, Any]]) -> None:
        """Tell the HR user who started the run that a draft needs approving.

        In-app only, not email: a draft is generated far too often per run
        for an inbox notification to stay useful, and HR is expected to be
        in the dashboard reviewing the run anyway. `_notify_hr_approval_needed`
        (candidate acted, HR must check) is a separate, much rarer signal and
        still goes out by email.
        """
        lead = bundle[0]
        run = await self._load_run()
        triggered_by = run.get("triggered_by_user_id")
        if not triggered_by:
            await self.log_step("notify_hr", "skipped", {"reason": "no_hr_user"})
            return

        step_label = lead.get("bundle_label") or lead.get("label") or lead["step_key"]
        # Bespoke copy is earned by what the step renders, not what it is
        # called: an org that re-created the LOI step still gets the LOI
        # copy, which carries the CTC and start date and is what makes it
        # reviewable at a glance. Everything an org composed itself gets the
        # generic notice, which names their step and lists its documents —
        # telling HR "the Appointment Letter and NDA are ready" about a step
        # they named something else is worse than saying nothing.
        if self._step_type_key(lead) == ob_catalog.DOCUMENT_TYPE_LOI:
            notif_type = "onboarding_loi_ready"
            title = f"LOI ready to sign — {run['candidate_name']}"
            body = f"{run['role_title']}, CTC {_format_ctc(run)}, starts {run['start_date']}."
        elif lead.get("bundle_key") == ob_catalog.BUNDLE_APPOINTMENT:
            notif_type = "onboarding_offer_bundle_ready"
            title = f"Appointment letter + NDA ready — {run['candidate_name']}"
            body = f"{run['role_title']} — waiting for your approval."
        else:
            notif_type = "onboarding_step_review_ready"
            title = f"{step_label} ready to review — {run['candidate_name']}"
            document_labels = [m.get("label") or m["step_key"] for m in bundle]
            body = ", ".join(document_labels) if document_labels else None

        try:
            await create_notification(
                org_id=self.org_id,
                user_id=triggered_by,
                type=notif_type,
                title=title,
                body=body,
                metadata={
                    "run_id": self.onboarding_run_id,
                    "step_key": lead["step_key"],
                    "step_label": step_label,
                },
                link_url=f"/onboarding/{self.onboarding_run_id}",
                dedupe_key=f"review-{self.onboarding_run_id}-{lead['step_key']}",
            )
            await self.log_step("notify_hr", "completed")
        except Exception as exc:  # noqa: BLE001
            await self.log_step("notify_hr", "failed", error=str(exc))

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
