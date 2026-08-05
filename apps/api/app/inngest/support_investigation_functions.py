"""Support "needs investigation" Inngest pipeline.

Two functions cover the branch CustomerSupportAgent's triage step can take
instead of drafting immediately (see customer_support_agent.py):

  * support_investigation_reminder_fn -- fired the moment a ticket parks in
    `needs_investigation` (support/investigation-started). Sleeps a fixed
    window, then pings the org's escalation Slack channel once if the
    ticket is still sitting there unpicked-up. One-shot by design: the
    gate exists for customer-safety (don't auto-answer what the knowledge
    base can't know), and a gate nobody ever gets nudged about is worse
    than the eager-draft behavior it replaced. A single nudge is enough
    for v1 -- a repeating nag loop can follow once there's real ticket
    volume to tell us it's needed.
  * support_investigation_draft_fn -- fired once a maintainer submits their
    findings via POST /admin/support/{id}/investigate
    (support/investigation-submitted). Wraps SupportInvestigationDraftAgent,
    which drafts the customer reply from the original email + the
    maintainer's notes and always parks the result in pending_review --
    this path never autonomous-sends, regardless of support_settings.mode.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.customer_support_agent import SupportInvestigationDraftAgent
from app.services.integrations import slack as slack_service

log = get_logger(__name__)

_inngest_client = get_inngest_client()

# How long a ticket sits in `needs_investigation` before we nag the
# escalation channel once. Tunable without a migration.
_REMINDER_DELAY = timedelta(hours=4)


def _load_ticket(ticket_id: str, org_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = (
        svc.table("support_tickets")
        .select("status, subject, from_email")
        .eq("id", ticket_id).eq("org_id", org_id).maybe_single().execute()
    )
    return (res.data if res else None) or None


def _load_escalation_channel(org_id: str) -> str | None:
    svc = get_service_client()
    res = (
        svc.table("support_settings")
        .select("escalation_channel_id")
        .eq("org_id", org_id).maybe_single().execute()
    )
    row = (res.data if res else None) or None
    return (row or {}).get("escalation_channel_id")


@_inngest_client.create_function(
    fn_id="support-investigation-reminder",
    trigger=inngest.TriggerEvent(event="support/investigation-started"),
    retries=2,
)
async def support_investigation_reminder_fn(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    ticket_id = data.get("ticket_id")
    org_id = data.get("org_id")
    if not ticket_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    await step.sleep(f"wait-{ticket_id}", _REMINDER_DELAY)

    row = await step.run(f"peek-ticket-{ticket_id}", lambda: _load_ticket(ticket_id, org_id))
    if not row or row.get("status") != "needs_investigation":
        return {"ticket_id": ticket_id, "status": "resolved_before_reminder"}

    channel_id = await step.run(
        f"load-channel-{ticket_id}", lambda: _load_escalation_channel(org_id),
    )
    if not channel_id:
        return {"ticket_id": ticket_id, "status": "no_escalation_channel"}

    async def _notify() -> None:
        hours = int(_REMINDER_DELAY.total_seconds() // 3600)
        await slack_service.post_message(
            org_id=org_id,
            channel_id=channel_id,
            text=(
                f":hourglass: Support ticket still needs investigation — "
                f"*{row.get('subject')}* from {row.get('from_email')} "
                f"has been waiting {hours}h+."
            ),
        )

    try:
        await step.run(f"notify-{ticket_id}", _notify)
    except Exception as exc:
        log.warning(
            "support_investigation_reminder_notify_failed org=%s ticket=%s err=%s",
            org_id, ticket_id, exc,
        )
        return {"ticket_id": ticket_id, "status": "notify_failed"}

    return {"ticket_id": ticket_id, "status": "reminded"}


@_inngest_client.create_function(
    fn_id="support-investigation-draft",
    trigger=inngest.TriggerEvent(event="support/investigation-submitted"),
    retries=2,
    # Drafting is a heavier multi-round LLM loop than the status update it
    # follows -- cap in-flight runs per org, matching customer-support-agent.
    concurrency=[inngest.Concurrency(limit=3, key="event.data.org_id", scope="fn")],
)
async def support_investigation_draft_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id = data.get("org_id")
    ticket_id = data.get("ticket_id")
    maintainer_prompt = (data.get("maintainer_prompt") or "").strip()
    if not org_id or not ticket_id or not maintainer_prompt:
        return {"status": "skipped", "reason": "missing_required_fields"}

    agent = SupportInvestigationDraftAgent(
        org_id=org_id,
        ticket_id=ticket_id,
        maintainer_prompt=maintainer_prompt,
        maintainer_user_id=data.get("maintainer_user_id"),
    )
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "support_investigation_draft_failed org=%s ticket=%s err=%s",
            org_id, ticket_id, exc,
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [support_investigation_reminder_fn, support_investigation_draft_fn]
