"""Customer support agent Inngest pipeline.

`customer_support_agent_fn` is triggered by `support/ticket-inbound`, fired
from `email_classify_functions.py` when an inbound email is classified as
`support` (previously that category was just logged and dropped). Wraps
`CustomerSupportAgent` — see that module for the actual triage / draft /
route logic.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.customer_support_agent import CustomerSupportAgent

log = get_logger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="customer-support-agent",
    trigger=inngest.TriggerEvent(event="support/ticket-inbound"),
    retries=2,
    # Drafting is a heavier multi-round LLM loop than classification; cap
    # in-flight runs per org so a burst of inbound support mail doesn't fan
    # out unbounded concurrent hybrid-search + LLM calls.
    concurrency=[inngest.Concurrency(limit=3, key="event.data.org_id", scope="fn")],
)
async def customer_support_agent_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id = data.get("org_id")
    if not org_id or not data.get("from_email") or not data.get("body"):
        return {"status": "skipped", "reason": "missing_required_fields"}

    agent = CustomerSupportAgent(org_id=org_id, ticket_input=dict(data))
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "customer_support_agent_run_failed",
            org_id=org_id, error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [customer_support_agent_fn]
