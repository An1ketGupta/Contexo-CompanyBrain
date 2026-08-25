"""Policy-propagation Inngest pipeline (Agent Day 9).

Wraps `PolicyPropagationAgent.run_safely()` in an Inngest function so the
work happens off the document-processing critical path. We bound
concurrency to one in-flight run per (document_id) so a fast re-upload of
the same policy doesn't double-post to Slack or double-fan acks.

The trigger event is emitted from `inngest/functions.py::process_document`
after a doc is marked `ready` AND the cheap `should_propagate` pre-check
passes (policy tag OR `requires_acknowledgement=true`). Admin-driven
manual re-runs use the same event with `triggered_by='manual'`.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.policy_propagation_agent import (
    PolicyPropagationAgent,
    should_propagate,
)

log = get_logger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="propagate-policy-change",
    trigger=inngest.TriggerEvent(event="agent/policy-propagate"),
    retries=2,
    concurrency=[
        # One in-flight propagation per document. A fast double-fire on the
        # same doc (re-upload + retry) coalesces into a single audit row.
        inngest.Concurrency(limit=1, key="event.data.document_id", scope="fn"),
    ],
)
async def propagate_policy_change(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    document_id = data.get("document_id")
    org_id = data.get("org_id")
    version_id = data.get("version_id")

    if not document_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    # Re-check the gate inside the function. The producer ran the same check,
    # but a manual re-fire from the admin UI bypasses that — keep the agent
    # honest: if the doc no longer qualifies (admin removed the tag), bail.
    qualifies = await ctx.step.run(
        "check-eligibility",
        lambda: should_propagate(document_id=document_id, org_id=org_id),
    )
    if not qualifies:
        return {"status": "skipped", "reason": "no_longer_policy"}

    agent = PolicyPropagationAgent(
        org_id=org_id,
        document_id=document_id,
        version_id=version_id,
    )
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "policy_propagation_failed",
            org_id=org_id,
            document_id=document_id,
            error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [propagate_policy_change]
