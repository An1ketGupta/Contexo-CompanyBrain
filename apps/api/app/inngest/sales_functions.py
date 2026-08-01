"""Sales outreach agent Inngest pipeline.

Two event-driven functions wrapping `SalesOutreachAgent`, plus the daily
cadence cron that is the agent's only genuinely autonomous trigger — every
other run starts from an admin action (importing leads, logging a reply).

The cron is where the "keep following up until they answer" behaviour lives.
It deliberately owns the give-up rule too: after `max_follow_ups` unanswered
nudges a lead is marked `lost` rather than nudged forever, and each org's
`daily_send_cap` bounds how many leads one run can touch, so a 5,000-row CSV
import can't turn into 5,000 drafts (and 5,000 LLM runs) the next morning.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.sales_outreach_agent import SalesOutreachAgent

log = get_logger(__name__)

_inngest_client = get_inngest_client()


async def _drive_agent(*, org_id: str, lead_id: str, kind: str) -> dict[str, Any]:
    agent = SalesOutreachAgent(org_id=org_id, lead_id=lead_id, kind=kind)
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "sales_outreach_agent_run_failed",
            org_id=org_id, lead_id=lead_id, kind=kind, error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


@_inngest_client.create_function(
    fn_id="sales-lead-outreach",
    trigger=inngest.TriggerEvent(event="sales/lead-outreach"),
    retries=2,
    # Drafting is a multi-round LLM + hybrid-search loop. Cap in-flight runs
    # per org so a bulk import doesn't fan out unbounded concurrent calls,
    # matching the customer support agent's posture.
    concurrency=[inngest.Concurrency(limit=3, key="event.data.org_id", scope="fn")],
)
async def sales_lead_outreach_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id = data.get("org_id")
    lead_id = data.get("lead_id")
    if not org_id or not lead_id:
        return {"status": "skipped", "reason": "missing_required_fields"}
    return await _drive_agent(
        org_id=org_id, lead_id=lead_id, kind=data.get("kind") or "first_touch",
    )


@_inngest_client.create_function(
    fn_id="sales-lead-reply",
    trigger=inngest.TriggerEvent(event="sales/lead-reply-logged"),
    retries=2,
    # Serialised per lead: two replies logged in quick succession must not
    # produce two drafts racing on the same thread.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.lead_id", scope="fn")],
)
async def sales_lead_reply_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id = data.get("org_id")
    lead_id = data.get("lead_id")
    if not org_id or not lead_id:
        return {"status": "skipped", "reason": "missing_required_fields"}
    return await _drive_agent(org_id=org_id, lead_id=lead_id, kind="reply")


# ── Daily follow-up cadence ───────────────────────────────────────────────


def _due_leads() -> list[dict[str, Any]]:
    """Leads whose follow-up clock has expired, newest cohort first.

    Reads across orgs in one query (service role, cron context) and lets the
    caller bucket by org to apply each org's own cap.
    """
    svc = get_service_client()
    res = (
        svc.table("sales_leads")
        .select("id, org_id, follow_up_count, status")
        .eq("status", "awaiting_reply")
        .lte("next_follow_up_at", datetime.now(UTC).isoformat())
        .order("next_follow_up_at")
        .limit(2000)
        .execute()
    )
    return res.data or []


def _org_settings(org_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not org_ids:
        return {}
    svc = get_service_client()
    res = (
        svc.table("sales_settings")
        .select("org_id, enabled, max_follow_ups, daily_send_cap")
        .in_("org_id", org_ids)
        .execute()
    )
    return {r["org_id"]: r for r in (res.data or [])}


def _mark_lost(lead_ids: list[str]) -> None:
    if not lead_ids:
        return
    svc = get_service_client()
    (
        svc.table("sales_leads")
        .update({
            "status": "lost",
            "escalation_reason": "no_reply_after_max_follow_ups",
            "next_follow_up_at": None,
        })
        .in_("id", lead_ids)
        .execute()
    )


@_inngest_client.create_function(
    fn_id="sales-cadence-check",
    # 08:00 UTC daily. Drafts land in the review queue before most reviewers'
    # working day rather than overnight.
    trigger=inngest.TriggerCron(cron="0 8 * * *"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=1)],
)
async def sales_cadence_check(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step

    due = await step.run("find-due-leads", lambda: asyncio.to_thread(_due_leads))
    if not due:
        return {"due": 0, "queued": 0, "closed": 0}

    settings_by_org = await step.run(
        "load-settings",
        lambda: asyncio.to_thread(
            _org_settings, sorted({row["org_id"] for row in due})
        ),
    )

    to_queue: list[dict[str, Any]] = []
    to_close: list[str] = []
    per_org_queued: dict[str, int] = {}

    for row in due:
        cfg = settings_by_org.get(row["org_id"])
        # An org that disabled the agent (or never enabled it) keeps its rows
        # untouched rather than having them silently marked lost.
        if not cfg or not cfg.get("enabled"):
            continue

        max_follow_ups = int(cfg.get("max_follow_ups") or 3)
        if int(row.get("follow_up_count") or 0) >= max_follow_ups:
            to_close.append(row["id"])
            continue

        cap = int(cfg.get("daily_send_cap") or 20)
        if per_org_queued.get(row["org_id"], 0) >= cap:
            continue
        per_org_queued[row["org_id"]] = per_org_queued.get(row["org_id"], 0) + 1
        to_queue.append(row)

    if to_close:
        await step.run("close-exhausted", lambda: asyncio.to_thread(_mark_lost, to_close))

    if to_queue:
        # One deterministic event id per lead per day so a cron retry (or a
        # duplicate schedule fire) can't draft the same follow-up twice.
        today = datetime.now(UTC).date().isoformat()
        await _inngest_client.send([
            inngest.Event(
                name="sales/lead-outreach",
                data={"org_id": r["org_id"], "lead_id": r["id"], "kind": "follow_up"},
                id=f"sales-followup-{r['id']}-{today}",
            )
            for r in to_queue
        ])

    log.info(
        "sales.cadence.ran",
        due=len(due), queued=len(to_queue), closed=len(to_close),
    )
    return {"due": len(due), "queued": len(to_queue), "closed": len(to_close)}


FUNCTIONS = [sales_lead_outreach_fn, sales_lead_reply_fn, sales_cadence_check]
