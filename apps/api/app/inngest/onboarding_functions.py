"""Onboarding-agent Inngest pipeline (Agent Day 8).

Wraps OnboardingAgent.run_safely() in an Inngest function so the work
happens asynchronously after invite acceptance. Concurrency capped at 1
per org_id to prevent two parallel agents stepping on each other when
admins accept multiple invites at once.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.onboarding_agent import OnboardingAgent

log = get_logger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="onboard-new-member",
    trigger=inngest.TriggerEvent(event="org/member-joined"),
    retries=2,
    # One onboarding run in flight per org. Keeps the LLM token spike
    # bounded if a bulk-import script invites 50 people back-to-back.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id", scope="fn")],
)
async def onboard_new_member(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    if not data.get("user_id") or not data.get("org_id"):
        return {"status": "skipped", "reason": "missing_required_fields"}

    agent = OnboardingAgent(org_id=data["org_id"], hire_data=data)
    try:
        result = await agent.run_safely()
    except Exception as exc:
        # `run_safely` already stamped the agent_runs row as failed —
        # rethrow so Inngest retries with backoff.
        log.warning(
            "onboarding_agent_run_failed",
            org_id=data["org_id"],
            user_id=data["user_id"],
            error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [onboard_new_member]
