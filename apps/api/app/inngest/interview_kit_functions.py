"""Inngest pipeline for the InterviewKitAgent.

One event today:
  recruiting/interview-kit-generate — fired by the FastAPI router when a
    recruiter clicks "Generate Interview Kit" on a requisition.

Concurrency: 1 per kit_id, so a double-click or retry can't double-run the
same kit. Retries=1 since LLM hiccups are usually transient — but we don't
want to burn a quota loop on a structural bug, so we don't retry harder.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.interview_kit import InterviewKitAgent

log = get_logger(__name__)
_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="recruiting-interview-kit-generate",
    trigger=inngest.TriggerEvent(event="recruiting/interview-kit-generate"),
    retries=1,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.kit_id", scope="fn"),
    ],
)
async def interview_kit_generate(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    kit_id = data.get("kit_id")
    org_id = data.get("org_id")
    requisition_id = data.get("requisition_id")
    if not (kit_id and org_id and requisition_id):
        return {"status": "skipped", "reason": "missing_event_data"}

    agent = InterviewKitAgent(
        org_id=org_id,
        requisition_id=requisition_id,
        kit_id=kit_id,
        jd_variant_index=data.get("jd_variant_index"),
        triggered_by_user_id=data.get("triggered_by_user_id"),
    )
    try:
        return await agent.run_safely()
    except Exception as exc:
        log.warning(
            "interview_kit.agent_run_failed kit=%s err=%s", kit_id, exc
        )
        # Re-raise so Inngest records the failure; BaseAgent + agent.run()
        # have already stamped both agent_runs and interview_kits as failed.
        raise


FUNCTIONS = [interview_kit_generate]
