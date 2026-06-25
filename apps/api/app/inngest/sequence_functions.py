"""Inngest worker for follow-up sequences (Agent2 Day 4 #8).

One function:

    sequence-dispatch  (event: sequence/scheduled)
        Loads all steps for the sequence, sleeps until each step's
        scheduled_for, then calls sequence_service.send_step which
        does the Gmail send + state update.

Why one function with step.sleep_until per step (not one Inngest run
per step):
    * Keeps the entire sequence's lifecycle visible as one Inngest run
      in the dashboard — easier to debug "why didn't step 2 send?"
    * step.sleep_until guarantees the wait is durable across worker
      restarts; we don't need a separate cron to scan for due steps.
    * Cancelling is just flipping statuses in the DB; the next step
      check in send_step returns 'sequence_cancelled' and the run
      exits cleanly.

Retries: send_step raises on transient failures (network, 5xx). Inngest
retries the step (not the whole sequence) automatically. Permanent
errors (Gmail revoked, body too large) are caught in send_step and the
step row is marked failed; the run continues to the next step.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.sequences import send_step

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="sequence-dispatch",
    trigger=inngest.TriggerEvent(event="sequence/scheduled"),
    # One run per sequence — keyed concurrency so a double-fire of
    # `sequence/scheduled` (manual re-schedule, retry) doesn't produce
    # two parallel runners.
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.sequence_id", scope="fn"),
    ],
    retries=2,
)
async def sequence_dispatch(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    sequence_id: str = data["sequence_id"]

    def _load_steps() -> list[dict[str, Any]]:
        svc = get_service_client()
        res = (
            svc.table("sequence_steps")
            .select("id, step_order, scheduled_for, status")
            .eq("sequence_id", sequence_id)
            .order("step_order", desc=False)
            .execute()
        )
        return res.data or []

    rows = await step.run(f"load-steps-{sequence_id}", _load_steps)
    if not rows:
        return {"sequence_id": sequence_id, "status": "no_steps"}

    sent = 0
    cancelled = False
    for row in rows:
        step_id = row["id"]
        scheduled_for = row.get("scheduled_for")
        if not scheduled_for or row["status"] in ("cancelled", "failed", "sent"):
            continue

        # Sleep until the step's send time. step.sleep_until is durable —
        # the worker can be torn down and restarted; the wait resumes.
        try:
            wake_at = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
        except ValueError:
            log.warning(
                "sequences.bad_scheduled_for step=%s value=%s",
                step_id,
                scheduled_for,
            )
            continue

        await step.sleep_until(f"sleep-{step_id}", wake_at)

        # Cancellation check before each send. send_step also checks, but
        # short-circuiting here saves the function call entirely.
        def _peek_status(_step_id: str = step_id) -> str:
            svc = get_service_client()
            res = (
                svc.table("sequence_steps")
                .select("status")
                .eq("id", _step_id)
                .maybe_single()
                .execute()
            )
            return (res.data or {}).get("status", "missing") if res else "missing"

        cur = await step.run(f"peek-status-{step_id}", _peek_status)
        if cur in ("cancelled", "sent"):
            if cur == "cancelled":
                cancelled = True
            continue

        result = await step.run(
            f"send-step-{step_id}",
            lambda step_id=step_id: send_step(step_id=step_id),
        )
        if (result or {}).get("status") == "sent":
            sent += 1
        elif (result or {}).get("status") == "sequence_cancelled":
            cancelled = True
            break

    return {
        "sequence_id": sequence_id,
        "sent": sent,
        "cancelled": cancelled,
    }


FUNCTIONS = [sequence_dispatch]
