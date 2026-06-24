"""Inngest functions for the Autoflow engine (Agent2 Day 1).

Functions exported here:

  * ``autoflow_dispatch_trigger`` — fan-out for ``autoflow/trigger.fired``.
    Any in-app emit (document.ready, knowledge_gap.detected, …) calls
    ``services.autoflow_service.emit_trigger`` which sends one of these.
    The function looks up matching autoflows for the (org, trigger_type)
    and kicks off one ``autoflow/run.requested`` per match. Splitting
    fan-out from execution keeps the per-autoflow concurrency key clean.

  * ``autoflow_execute_run`` — handles ``autoflow/run.requested``. One
    function call = one autoflow run. Concurrency keyed on autoflow_id
    so a burst of triggers for the same flow serialises.

  * ``autoflow_scheduled_dispatcher`` — single cron that fires every
    minute, walks active scheduled autoflows, and enqueues runs for the
    ones whose cron matches the current minute.

  * ``autoflow_resume_handler`` — handles ``autoflow/resume`` (fired by
    the approvals dispatcher when an autoflow-channel approval is approved)
    AND watches ``approval/resolved`` so that *rejected* approvals also
    cancel the held run.

Retries:
    The executor itself doesn't retry on internal action failures (those
    are terminal per design — see autoflow_service docstring). Inngest's
    function-level retries (default 3) cover infra blips like a Supabase
    connection reset.

Why not one mega-function that does both dispatch and execute?
    Concurrency keys are per-function. If we shared a function, fan-out
    would compete for the same slots as execution and a hot trigger could
    starve cold autoflows. Two functions, two key-spaces.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.autoflow_service import (
    execute_autoflow_run,
    get_matching_autoflows,
    resume_run_after_approval,
    scheduled_autoflows_due_now,
)

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# ── Fan-out: trigger → matching autoflows → run.requested events ─────────


@_inngest_client.create_function(
    fn_id="autoflow-dispatch-trigger",
    trigger=inngest.TriggerEvent(event="autoflow/trigger.fired"),
    retries=2,
    # Fan-out is cheap; allow generous parallelism so a spike of triggers
    # from a doc-ingest burst doesn't backlog. Real cost lives in the
    # execute function below, which has its own per-autoflow key.
    concurrency=[inngest.Concurrency(limit=16)],
)
async def autoflow_dispatch_trigger(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data or {}
    org_id = data.get("org_id")
    trigger_type = data.get("trigger_type")
    payload = data.get("payload") or {}
    if not org_id or not trigger_type:
        log.warning("autoflow.trigger.missing_fields", data=data)
        return {"status": "skipped", "reason": "missing_fields"}

    matching = await ctx.step.run(
        "fetch-matching",
        lambda: get_matching_autoflows(org_id=org_id, trigger_type=trigger_type, payload=payload),
    )

    if not matching:
        return {"status": "no_match", "trigger_type": trigger_type}

    client = get_inngest_client()
    enqueued = 0
    for autoflow in matching:
        # One event per autoflow so the executor's concurrency key isolates
        # by autoflow_id rather than colliding on a fan-out batch.
        await client.send(
            inngest.Event(
                name="autoflow/run.requested",
                data={
                    "autoflow_id": autoflow["id"],
                    "org_id": org_id,
                    "trigger_payload": payload,
                    "source": "trigger",
                },
            )
        )
        enqueued += 1

    return {"status": "ok", "enqueued": enqueued, "trigger_type": trigger_type}


# ── Execute one run ──────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="autoflow-execute-run",
    trigger=inngest.TriggerEvent(event="autoflow/run.requested"),
    retries=2,
    # Serialise per autoflow. Two simultaneous runs of the same autoflow
    # would race on external API side-effects (Slack post, email send) and
    # produce duplicate outputs without an idempotency key in every adapter.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.autoflow_id")],
)
async def autoflow_execute_run(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data or {}
    autoflow_id = data.get("autoflow_id")
    org_id = data.get("org_id")
    trigger_payload = data.get("trigger_payload") or {}

    if not autoflow_id or not org_id:
        log.warning("autoflow.execute.missing_fields", data=data)
        return {"status": "skipped", "reason": "missing_fields"}

    autoflow = await ctx.step.run(
        "fetch-autoflow",
        lambda: _fetch_autoflow(autoflow_id),
    )
    if not autoflow:
        return {"status": "not_found", "autoflow_id": autoflow_id}
    if not autoflow.get("is_active"):
        return {"status": "inactive", "autoflow_id": autoflow_id}

    result = await ctx.step.run(
        "execute",
        lambda: execute_autoflow_run(
            autoflow=autoflow,
            trigger_payload=trigger_payload,
        ),
    )
    return result


# ── Scheduled cron — one runner, evaluates every active scheduled autoflow


@_inngest_client.create_function(
    fn_id="autoflow-scheduled-dispatcher",
    # Every minute. The matcher dedupes via last_fired_at so a slow run
    # can't double-fire across dispatcher restarts.
    trigger=inngest.TriggerCron(cron="* * * * *"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=1)],
)
async def autoflow_scheduled_dispatcher(ctx: inngest.Context) -> dict[str, Any]:
    due = await ctx.step.run("find-due", scheduled_autoflows_due_now)
    if not due:
        return {"status": "ok", "fired": 0}

    client = get_inngest_client()
    fired = 0
    for autoflow in due:
        await client.send(
            inngest.Event(
                name="autoflow/run.requested",
                data={
                    "autoflow_id": autoflow["id"],
                    "org_id": autoflow["org_id"],
                    "trigger_payload": {"scheduled_at": ctx.event.id},
                    "source": "scheduled",
                },
            )
        )
        fired += 1
    log.info("autoflow.scheduled.fired", count=fired)
    return {"status": "ok", "fired": fired}


# ── Resume after approval ───────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="autoflow-resume",
    trigger=inngest.TriggerEvent(event="autoflow/resume"),
    retries=2,
    concurrency=[inngest.Concurrency(limit=1, key="event.data.autoflow_run_id")],
)
async def autoflow_resume_handler(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data or {}
    approval_id = data.get("approval_id")
    approved = bool(data.get("approved", True))
    if not approval_id:
        return {"status": "skipped", "reason": "missing_approval_id"}

    result = await ctx.step.run(
        "resume",
        lambda: resume_run_after_approval(approval_id=approval_id, approved=approved),
    )
    return result


@_inngest_client.create_function(
    fn_id="autoflow-on-approval-rejected",
    trigger=inngest.TriggerEvent(event="approval/resolved"),
    retries=2,
)
async def autoflow_on_approval_rejected(ctx: inngest.Context) -> dict[str, Any]:
    """Cancel a held autoflow_run when its approval is *rejected*.

    The approve path is covered by the autoflow channel in dispatch_execution
    (which fires autoflow/resume directly). Reject doesn't go through
    dispatch_execution — it just notifies the requester via approval/resolved.
    So we listen here and cancel the held run.
    """
    data = ctx.event.data or {}
    approval_id = data.get("approval_id")
    action = data.get("action")
    if not approval_id or action != "rejected":
        return {"status": "skipped"}

    # Cheap: only do anything if this was an autoflow-channel approval.
    def _is_autoflow_approval() -> dict[str, Any] | None:
        svc = get_service_client()
        res = (
            svc.table("approvals")
            .select("execution_action, subject_type")
            .eq("id", approval_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await ctx.step.run("lookup", _is_autoflow_approval)
    if not row:
        return {"status": "not_found"}
    channel = (row.get("execution_action") or {}).get("channel")
    if channel != "autoflow":
        return {"status": "not_autoflow"}

    result = await ctx.step.run(
        "cancel",
        lambda: resume_run_after_approval(approval_id=approval_id, approved=False),
    )
    return result


# ── Helpers ──────────────────────────────────────────────────────────────


def _fetch_autoflow(autoflow_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = svc.table("autoflows").select("*").eq("id", autoflow_id).maybe_single().execute()
    return res.data if res else None


FUNCTIONS = [
    autoflow_dispatch_trigger,
    autoflow_execute_run,
    autoflow_scheduled_dispatcher,
    autoflow_resume_handler,
    autoflow_on_approval_rejected,
]
