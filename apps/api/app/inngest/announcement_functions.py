"""Inngest worker for internal announcements (Agent2 Day 4 #23).

Single function:

    announcement-dispatch  (event: internal_announcement/scheduled)
        Sleeps until the announcement's scheduled_for, then calls
        internal_comms.dispatch_announcement which fans out to email
        + Slack + Notion concurrently.

Why one function vs three (one per channel):
    The user picked these three channels because they want the message
    to land at roughly the same instant. Fanning into three independent
    Inngest functions means three independent sleep budgets and three
    independent retry policies — which is exactly the kind of skew
    that turns "all-hands recap at 10am" into "email at 10:00, Slack at
    10:08, Notion at 10:15." One function = one wake = simultaneous fan.

The dispatcher itself runs the three sends with asyncio.gather, so a
slow Slack post can't delay the email.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.internal_comms import dispatch_announcement

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="announcement-dispatch",
    trigger=inngest.TriggerEvent(event="internal_announcement/scheduled"),
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.announcement_id", scope="fn"),
    ],
    retries=2,
)
async def announcement_dispatch(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    announcement_id: str = data["announcement_id"]

    def _load() -> dict[str, Any] | None:
        svc = get_service_client()
        res = (
            svc.table("internal_announcements")
            .select("id, status, scheduled_for")
            .eq("id", announcement_id)
            .maybe_single()
            .execute()
        )
        return (res.data if res else None) or None

    row = await step.run(f"load-announcement-{announcement_id}", _load)
    if not row:
        return {"announcement_id": announcement_id, "status": "not_found"}

    if row["status"] == "cancelled":
        return {"announcement_id": announcement_id, "status": "cancelled_before_sleep"}
    if row["status"] != "scheduled":
        return {"announcement_id": announcement_id, "status": f"unexpected:{row['status']}"}

    scheduled_for = row.get("scheduled_for")
    if scheduled_for:
        try:
            wake_at = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
            await step.sleep_until(f"sleep-{announcement_id}", wake_at)
        except ValueError:
            log.warning(
                "announcements.bad_scheduled_for announcement=%s value=%s",
                announcement_id,
                scheduled_for,
            )

    # One more cancellation check before we fire.
    cur_status = await step.run(
        f"peek-status-{announcement_id}",
        lambda: _peek_status(announcement_id),
    )
    if cur_status == "cancelled":
        return {"announcement_id": announcement_id, "status": "cancelled_before_dispatch"}

    result = await step.run(
        f"dispatch-{announcement_id}",
        lambda: dispatch_announcement(announcement_id=announcement_id),
    )
    return {"announcement_id": announcement_id, **(result or {})}


def _peek_status(announcement_id: str) -> str:
    svc = get_service_client()
    res = (
        svc.table("internal_announcements")
        .select("status")
        .eq("id", announcement_id)
        .maybe_single()
        .execute()
    )
    return (res.data or {}).get("status", "missing") if res else "missing"


FUNCTIONS = [announcement_dispatch]
