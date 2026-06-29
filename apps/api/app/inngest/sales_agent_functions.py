"""Inngest pipeline for the SalesAgent.

Events:
  sales/lead-entered           — rep created a new deal
  sales/outreach-approved      — rep approved the outreach draft → agent sends
  sales/reply-received         — gmail watch detected an inbound reply
  sales/meeting-booked         — rep marked a meeting as scheduled
  sales/transcript-submitted   — rep pasted a call transcript
  sales/next-step-chosen       — rep picked propose/another_call/qualify_out
  sales/proposal-approved      — rep approved the proposal draft → mark sent
  sales/template-uploaded      — admin uploaded a previously-missing template
  sales/resume                 — generic re-kick

Crons:
  daily 09:00 UTC — find awaiting_reply deals due for follow-up
  daily 10:00 UTC — find awaiting_decision deals due for check-in
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.sales_agent import SalesAgent
from app.services.agents.sales_agent import followups as fu_svc

log = get_logger(__name__)
_inngest_client = get_inngest_client()


async def _drive(
    *, deal_run_id: str, org_id: str, triggered_by_user_id: str | None = None
) -> dict[str, Any]:
    agent = SalesAgent(
        org_id=org_id,
        deal_run_id=deal_run_id,
        triggered_by_user_id=triggered_by_user_id,
    )
    try:
        return await agent.run_safely()
    except Exception as exc:
        log.warning("sales.agent_run_failed deal=%s err=%s", deal_run_id, exc)
        raise


@_inngest_client.create_function(
    fn_id="sales-lead-entered",
    trigger=inngest.TriggerEvent(event="sales/lead-entered"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_lead_entered(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(
        deal_run_id=deal_run_id,
        org_id=org_id,
        triggered_by_user_id=data.get("triggered_by_user_id"),
    )


@_inngest_client.create_function(
    fn_id="sales-outreach-approved",
    trigger=inngest.TriggerEvent(event="sales/outreach-approved"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_outreach_approved(ctx: inngest.Context) -> dict[str, Any]:
    """Router has already attempted the send + stamped outreach_sent_at,
    thread_id, message_id. We just drive the agent which transitions to
    awaiting_reply and schedules the first follow-up."""
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="sales-reply-received",
    trigger=inngest.TriggerEvent(event="sales/reply-received"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_reply_received(ctx: inngest.Context) -> dict[str, Any]:
    """Gmail watch fired — a thread we own got a new inbound message. The
    webhook handler already stamped reply_received_at. We log + notify (the
    rep takes over from here)."""
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="sales-meeting-booked",
    trigger=inngest.TriggerEvent(event="sales/meeting-booked"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_meeting_booked(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="sales-transcript-submitted",
    trigger=inngest.TriggerEvent(event="sales/transcript-submitted"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_transcript_submitted(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    transcript = data.get("transcript") or ""
    if not deal_run_id or not org_id or not transcript:
        return {"status": "skipped"}
    agent = SalesAgent(org_id=org_id, deal_run_id=deal_run_id)
    await agent.create_run_record()
    try:
        return await agent.step_process_transcript(transcript)
    except Exception as exc:  # noqa: BLE001
        await agent.fail(str(exc))
        raise


@_inngest_client.create_function(
    fn_id="sales-next-step-chosen",
    trigger=inngest.TriggerEvent(event="sales/next-step-chosen"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_next_step_chosen(ctx: inngest.Context) -> dict[str, Any]:
    """Rep chose propose | another_call | qualify_out. Router already updated
    deal_runs.status accordingly:
      * propose → proposal_drafting (and we drive into the gen step here)
      * another_call → awaiting next meeting (the rep books it)
      * qualify_out → closed_lost
    """
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="sales-proposal-approved",
    trigger=inngest.TriggerEvent(event="sales/proposal-approved"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_proposal_approved(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="sales-template-uploaded",
    trigger=inngest.TriggerEvent(event="sales/template-uploaded"),
    retries=2,
)
async def sales_template_uploaded(ctx: inngest.Context) -> dict[str, Any]:
    """Resume any deals stuck on the corresponding template_kind in this org."""
    data = ctx.event.data
    org_id = data.get("org_id")
    template_kind = data.get("template_kind")
    if not org_id or not template_kind:
        return {"status": "skipped"}

    svc = get_service_client()
    # Block status varies: icp -> blocked_missing_icp_doc; others -> blocked_missing_template
    blocked_status = (
        "blocked_missing_icp_doc" if template_kind == "icp" else "blocked_missing_template"
    )
    q = svc.table("deal_runs").select("id").eq("org_id", org_id).eq("status", blocked_status)
    if template_kind != "icp":
        q = q.eq("blocked_template_kind", template_kind)
    res = q.execute()
    rows = res.data or []
    resumed = 0
    for r in rows:
        try:
            await _drive(deal_run_id=r["id"], org_id=org_id)
            resumed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.resume_failed deal=%s err=%s", r["id"], exc)
    return {"status": "ok", "resumed": resumed, "template_kind": template_kind}


@_inngest_client.create_function(
    fn_id="sales-resume",
    trigger=inngest.TriggerEvent(event="sales/resume"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.deal_run_id", scope="fn"),
    ],
)
async def sales_resume(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    deal_run_id = data.get("deal_run_id")
    org_id = data.get("org_id")
    if not deal_run_id or not org_id:
        return {"status": "skipped"}
    return await _drive(deal_run_id=deal_run_id, org_id=org_id)


# ── Crons ──────────────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="sales-followup-cron",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),  # daily 09:00 UTC
    retries=1,
)
async def sales_followup_cron(ctx: inngest.Context) -> dict[str, Any]:
    """Drive follow-up drafts for deals that have been silent past cadence.
    Also closes deals where the cadence has been exhausted."""
    due = await fu_svc.find_deals_due_for_followup(limit=200)
    drafted = 0
    for row in due:
        agent = SalesAgent(org_id=row["org_id"], deal_run_id=row["id"])
        try:
            await agent.create_run_record()
            await agent.step_draft_followup()
            drafted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.followup_cron_failed deal=%s err=%s", row["id"], exc)

    to_close = await fu_svc.find_deals_due_for_close_no_reply(limit=200)
    closed = 0
    svc = get_service_client()
    for row in to_close:
        try:
            svc.table("deal_runs").update(
                {
                    "status": "no_reply_closed",
                    "closed_at": datetime.now(UTC).isoformat(),
                    "close_outcome": "lost",
                    "close_reason": "no_reply_after_followups",
                }
            ).eq("id", row["id"]).execute()
            closed += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.no_reply_close_failed deal=%s err=%s", row["id"], exc)

    return {"status": "ok", "drafted": drafted, "closed": closed}


@_inngest_client.create_function(
    fn_id="sales-checkin-cron",
    trigger=inngest.TriggerCron(cron="0 10 * * *"),  # daily 10:00 UTC
    retries=1,
)
async def sales_checkin_cron(ctx: inngest.Context) -> dict[str, Any]:
    due = await fu_svc.find_deals_due_for_checkin(limit=200)
    drafted = 0
    for row in due:
        agent = SalesAgent(org_id=row["org_id"], deal_run_id=row["id"])
        try:
            await agent.create_run_record()
            await agent.step_draft_checkin()
            drafted += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.checkin_cron_failed deal=%s err=%s", row["id"], exc)

    at_risk = await fu_svc.find_deals_at_risk(limit=200)
    flagged = 0
    svc = get_service_client()
    for row in at_risk:
        try:
            svc.table("deal_runs").update({"status": "at_risk"}).eq("id", row["id"]).execute()
            flagged += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("sales.at_risk_flag_failed deal=%s err=%s", row["id"], exc)

    return {"status": "ok", "drafted": drafted, "flagged_at_risk": flagged}


FUNCTIONS: list[Any] = [
    sales_lead_entered,
    sales_outreach_approved,
    sales_reply_received,
    sales_meeting_booked,
    sales_transcript_submitted,
    sales_next_step_chosen,
    sales_proposal_approved,
    sales_template_uploaded,
    sales_resume,
    sales_followup_cron,
    sales_checkin_cron,
]
