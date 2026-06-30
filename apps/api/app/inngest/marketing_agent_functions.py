"""Inngest pipeline for the MarketingAgent.

One event today:
  marketing/brief-generate — fired by the FastAPI router when a marketer
    clicks "Generate" on a new brief.

Concurrency: 1 per brief_id, so a double-click or retry can't double-run
the same brief. Retries=1 since LLM hiccups are usually transient — but we
don't want to burn a quota loop on a structural bug.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.marketing_agent import MarketingAgent

log = get_logger(__name__)
_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="marketing-brief-generate",
    trigger=inngest.TriggerEvent(event="marketing/brief-generate"),
    retries=1,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.brief_id", scope="fn"),
    ],
)
async def marketing_brief_generate(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    brief_id = data.get("brief_id")
    org_id = data.get("org_id")
    objective = data.get("objective")
    if not (brief_id and org_id and objective):
        return {"status": "skipped", "reason": "missing_event_data"}

    agent = MarketingAgent(
        org_id=org_id,
        brief_id=brief_id,
        objective=objective,
        audience_hint=data.get("audience_hint"),
        channels=data.get("channels") or [],
        competitors=data.get("competitors") or [],
        collection_id=data.get("collection_id"),
        triggered_by_user_id=data.get("triggered_by_user_id"),
        run_id=data.get("run_id"),
    )
    try:
        return await agent.run_safely()
    except Exception as exc:
        log.warning(
            "marketing_brief.agent_run_failed brief=%s err=%s", brief_id, exc
        )
        # Re-raise so Inngest records the failure; BaseAgent + agent.run()
        # have already stamped both agent_runs and marketing_briefs as failed.
        raise


FUNCTIONS = [marketing_brief_generate]
