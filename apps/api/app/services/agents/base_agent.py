"""BaseAgent — shared lifecycle wrapper for autonomous agents.

Each concrete agent inherits from this class and overrides `run()`. The
base class owns:

  * Creation of the `agent_runs` row before the agent starts.
  * Per-step logging — `log_step(name, status, result|error)` appends to
    the steps JSONB array so the admin audit page can render a true
    execution log with timestamps.
  * Completion / failure helpers that update the run row exactly once.
  * Token + confidence accounting — accumulators that get persisted on
    `complete()`.

Why a base class (instead of a function-per-agent):

  * Forces a uniform audit shape so the audit UI doesn't need to know
    about each agent's internals.
  * Encapsulates the "always write the run, even on crash" invariant —
    `run_safely()` wraps the user `run()` in try/except so a partial run
    still shows up as `status='failed'` with the steps that did execute.
  * Keeps the surface tiny (4 verbs: start, log_step, complete, fail) so
    agents stay readable.

Concurrency: agents run inside Inngest functions. We rely on Inngest's
retry + idempotency rather than database locking. Two retries hitting the
same `run_id` would both create new rows — to guard against that we let
the caller pass `run_id` (defaults to a fresh uuid).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)


class BaseAgent:
    """Lifecycle + audit-log wrapper for an autonomous agent run.

    Subclasses MUST override `run()`. They MAY override `agent_type` as
    a class attribute or pass it in the constructor.
    """

    agent_type: str = "base"

    def __init__(
        self,
        *,
        org_id: str,
        input_data: dict[str, Any],
        triggered_by: str = "user",
        triggered_by_user_id: str | None = None,
        agent_type: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.org_id = org_id
        self.input_data = input_data
        self.triggered_by = triggered_by
        self.triggered_by_user_id = triggered_by_user_id
        if agent_type:
            self.agent_type = agent_type
        self.run_id: str = run_id or str(uuid.uuid4())
        self.steps: list[dict[str, Any]] = []
        self.tokens_used: int = 0
        self.confidence_scores: list[float] = []
        self._created: bool = False

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def create_run_record(self) -> str:
        """Insert the agent_runs row. Idempotent per `run_id` — calling
        twice will fail on the primary-key constraint, which is the
        desired behavior (a retry should not double-create)."""
        svc = get_service_client()
        row = {
            "id": self.run_id,
            "org_id": self.org_id,
            "agent_type": self.agent_type,
            "triggered_by": self.triggered_by,
            "triggered_by_user_id": self.triggered_by_user_id,
            "status": "running",
            "input": self.input_data,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await asyncio.to_thread(
                lambda: svc.table("agent_runs").insert(row).execute()
            )
            self._created = True
        except Exception as exc:
            # On a retry the PK already exists — load the existing row's
            # steps so we append rather than restart from scratch.
            if "duplicate key" in str(exc).lower():
                log.info("agent_run_idempotent_retry", run_id=self.run_id)
                existing = await asyncio.to_thread(
                    lambda: svc.table("agent_runs")
                    .select("steps")
                    .eq("id", self.run_id)
                    .maybe_single()
                    .execute()
                )
                if existing and existing.data:
                    self.steps = existing.data.get("steps") or []
                self._created = True
            else:
                raise
        return self.run_id

    async def log_step(
        self,
        step_name: str,
        status: str,  # 'started' | 'completed' | 'failed' | 'skipped'
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Append a step record and persist. Cheap O(steps) per call —
        agents typically have ≤ 10 steps so this is fine; if a future
        agent needs hundreds of steps, switch to JSONB append via SQL."""
        step = {
            "step_name": step_name,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.steps.append(step)

        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update({"steps": self.steps})
            .eq("id", self.run_id)
            .execute()
        )

        log.info(
            "agent_step",
            run_id=self.run_id,
            agent_type=self.agent_type,
            step_name=step_name,
            status=status,
        )

    def add_tokens(self, n: int) -> None:
        """Accumulate tokens; persisted on `complete()`. Cheap & in-process."""
        if n > 0:
            self.tokens_used += int(n)

    def add_confidence(self, score: float | None) -> None:
        """Record a confidence score (0–10). None is ignored so callers
        don't need to gate on absence."""
        if score is None:
            return
        try:
            self.confidence_scores.append(float(score))
        except (TypeError, ValueError):
            pass

    async def complete(self, output: dict[str, Any] | None = None) -> None:
        """Mark the run completed. Idempotent — repeated calls overwrite
        completed_at, which is fine for retries. Also fires the Day-14
        lifecycle fan-out (org webhooks + per-request callback)."""
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "completed",
                    "output": output or {},
                    "steps": self.steps,
                    "llm_tokens_used": self.tokens_used,
                    "confidence_scores": self.confidence_scores,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", self.run_id)
            .execute()
        )
        await self._fire_lifecycle("completed", output=output, error=None)

    async def fail(self, error: str) -> None:
        """Mark the run failed and surface the error string. The steps
        array reflects exactly how far the run got."""
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "failed",
                    "error": error[:2000],
                    "steps": self.steps,
                    "llm_tokens_used": self.tokens_used,
                    "confidence_scores": self.confidence_scores,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", self.run_id)
            .execute()
        )
        await self._fire_lifecycle("failed", output=None, error=error)

    async def _fire_lifecycle(
        self,
        status: str,
        *,
        output: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        """Best-effort fan-out: org-configured webhooks + per-request
        callback URL. Imported lazily to avoid an import cycle."""
        try:
            from app.services.agent_callbacks import fire_agent_lifecycle_events

            api_context = None
            if isinstance(self.input_data, dict):
                api_context = self.input_data.get("_api_context")

            await fire_agent_lifecycle_events(
                run_id=self.run_id,
                org_id=self.org_id,
                agent_type=self.agent_type,
                status=status,
                output=output,
                error=error,
                api_context=api_context,
            )
        except Exception as exc:
            log.warning(
                "agent_lifecycle_fanout_failed run_id=%s err=%s",
                self.run_id,
                exc,
            )

    async def pause_for_approval(self, approval_id: str) -> None:
        """Park the run in `pending_approval` and cross-link the approval
        row. Used by agents that gate side effects on a human decision."""
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update(
                {
                    "status": "pending_approval",
                    "approval_id": approval_id,
                    "steps": self.steps,
                }
            )
            .eq("id", self.run_id)
            .execute()
        )

    # ── Entry point ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Override in subclasses. The base implementation just records
        an empty completion so a misconfigured agent doesn't leave a
        permanently-`running` row in the audit table."""
        raise NotImplementedError(
            "BaseAgent.run() must be overridden by concrete agents."
        )

    async def run_safely(self) -> dict[str, Any]:
        """Standard entry point for callers (Inngest workers). Ensures
        every code path leaves the run in a terminal state — `completed`
        on success, `failed` on exception, with steps preserved either
        way. Returns the agent's `run()` result on success, raises on
        failure (after stamping the row) so Inngest's retry can kick in."""
        if not self._created:
            await self.create_run_record()
        try:
            result = await self.run()
        except Exception as exc:
            log.exception("agent_run_failed", run_id=self.run_id, agent_type=self.agent_type)
            await self.fail(str(exc) or exc.__class__.__name__)
            raise
        # Allow `run()` to call `complete()` itself; if it didn't, we do
        # it here with whatever it returned.
        await self._complete_if_running(result if isinstance(result, dict) else {})
        return result if isinstance(result, dict) else {}

    async def _complete_if_running(self, output: dict[str, Any]) -> None:
        svc = get_service_client()
        row = await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .select("status")
            .eq("id", self.run_id)
            .maybe_single()
            .execute()
        )
        if row and row.data and row.data.get("status") == "running":
            await self.complete(output)
