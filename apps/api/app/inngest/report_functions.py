"""Inngest functions for scheduled reports (V5 #98).

Two functions:

1. `reports/dispatch-due` (cron */15) — scans `scheduled_reports` for rows
   where is_active AND next_send_at <= now(), then fans out a
   `reports/dispatch-one` event per row. Cron at 15-minute granularity is
   the right tradeoff: minutely fires would hammer the DB; hourly would
   make the send_time_utc field feel imprecise.

2. `reports/dispatch-one` — actually generates the report data, sends one
   email per recipient via the existing `email/send` event pipeline, then
   updates `last_sent_at` + recomputes `next_send_at`.

Idempotency: the dispatch cron always recomputes next_send_at strictly
after `now()`, so a late or duplicated fire from the cron can't double-send.
The per-report fanout also uses a per-fire dedupe_key on the email send so
the email_events guard rejects same-minute duplicates if the worker retries.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.report_scheduler import (
    compute_next_send_at,
    gather_knowledge_health,
    gather_usage_summary,
)

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# ── 1. Scan + fan-out cron ───────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="reports-dispatch-due",
    trigger=inngest.TriggerCron(cron="*/15 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=1,
)
async def dispatch_due_reports(ctx: inngest.Context) -> dict[str, Any]:
    """Every 15 minutes: find and fan out reports whose next_send_at has elapsed."""

    async def _load() -> list[dict[str, Any]]:
        svc = get_service_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        def _query() -> list[dict[str, Any]]:
            return (
                svc.table("scheduled_reports")
                .select("id, org_id")
                .eq("is_active", True)
                .lte("next_send_at", now_iso)
                .limit(500)
                .execute()
                .data
                or []
            )

        return await asyncio.to_thread(_query)

    due = await ctx.step.run("collect-due", _load)
    if not due:
        return {"due": 0}

    client = get_inngest_client()
    for r in due:
        await ctx.step.run(
            f"enqueue-{r['id']}",
            lambda r=r: client.send(
                inngest.Event(
                    name="reports/dispatch-one",
                    data={"report_id": r["id"]},
                )
            ),
        )
    return {"due": len(due)}


# ── 2. Per-report dispatcher ────────────────────────────────────────────────


def _window_for(frequency: str) -> int:
    """Lookback window the usage_summary report covers, by cadence."""
    return {"daily": 1, "weekly": 7, "monthly": 30}.get(frequency, 7)


def _load_report(report_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    return (
        svc.table("scheduled_reports")
        .select("*")
        .eq("id", report_id)
        .maybe_single()
        .execute()
        .data
    )


def _mark_sent(report_id: str, next_send_at: datetime) -> None:
    svc = get_service_client()
    now_iso = datetime.now(timezone.utc).isoformat()
    svc.table("scheduled_reports").update(
        {
            "last_sent_at": now_iso,
            "next_send_at": next_send_at.isoformat(),
        }
    ).eq("id", report_id).execute()


@_inngest_client.create_function(
    fn_id="reports-dispatch-one",
    trigger=inngest.TriggerEvent(event="reports/dispatch-one"),
    retries=2,
    concurrency=[inngest.Concurrency(limit=10)],
)
async def dispatch_one_report(ctx: inngest.Context) -> dict[str, Any]:
    """Render + send one scheduled report. Idempotent: if the row has already
    advanced past now (e.g. a faster competing worker grabbed it), we no-op."""
    from app.services.email import send_email_event

    data = ctx.event.data
    report_id: str = data["report_id"]
    forced: bool = bool(data.get("force"))

    report = await ctx.step.run("load-report", lambda: _load_report(report_id))
    if not report:
        return {"status": "skipped", "reason": "not_found"}
    if not report.get("is_active") and not forced:
        return {"status": "skipped", "reason": "inactive"}

    org_id: str = report["org_id"]
    report_type: str = report["report_type"]
    frequency: str = report["frequency"]
    recipients: list[str] = report.get("recipients") or []

    # ── Generate report data ──
    if report_type == "usage_summary":
        payload = await ctx.step.run(
            "gather-usage",
            lambda: gather_usage_summary(org_id, window_days=_window_for(frequency)),
        )
    elif report_type == "knowledge_health":
        payload = await ctx.step.run(
            "gather-health",
            lambda: gather_knowledge_health(org_id),
        )
    else:
        log.warning("scheduled_report_unknown_type", report_type=report_type)
        return {"status": "skipped", "reason": "unknown_type"}

    # ── Send email(s) ──
    # Per-fire dedupe_key (timestamp at minute granularity) so a retry of this
    # function lands inside the email_events guard window and short-circuits
    # rather than double-sending.
    fire_key = datetime.now(timezone.utc).strftime("rpt-%Y%m%dT%H%M-") + report_id[:8]
    event_type = "scheduled_usage_summary" if report_type == "usage_summary" else "scheduled_knowledge_health"

    sent = 0
    for recipient in recipients:
        try:
            await send_email_event(
                event_type=event_type,  # type: ignore[arg-type]
                to=recipient,
                user_id=report.get("created_by"),
                org_id=org_id,
                dedupe_key=fire_key,
                data={
                    "report_id": report_id,
                    "frequency": frequency,
                    "app_url": _app_url(),
                    **payload,
                },
            )
            sent += 1
        except Exception as exc:
            # One bad recipient shouldn't block the others; log and move on.
            log.warning(
                "scheduled_report_recipient_failed",
                report_id=report_id,
                recipient=recipient,
                error=str(exc),
            )

    # ── Advance the schedule ──
    # Always advance strictly after now() so a late cron fire doesn't re-fire
    # the same slot a few minutes later.
    next_send = compute_next_send_at(
        frequency=frequency,
        send_time_utc=int(report.get("send_time_utc") or 8),
        day_of_week=report.get("day_of_week"),
        day_of_month=report.get("day_of_month"),
    )

    if not forced:
        await ctx.step.run(
            "mark-sent",
            lambda: asyncio.to_thread(lambda: _mark_sent(report_id, next_send)),
        )

    return {"status": "sent", "recipients": sent, "next_send_at": next_send.isoformat()}


def _app_url() -> str:
    from app.config import get_settings
    return get_settings().app_url


FUNCTIONS = [dispatch_due_reports, dispatch_one_report]
