"""Inngest functions for Feature 2.2 — Proactive Morning Briefings.

Two functions:
  1. briefings-hourly-sweep  (cron: every hour at :00)
       Walk briefing_preferences and fan one event per recipient whose
       (weekday, hour, timezone) matches "right now".
  2. briefings-deliver       (event: briefings/deliver)
       Generate + persist + dispatch one briefing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.services.briefings import (
    current_period_key,
    find_due_recipients,
    generate_briefing_for_user,
)

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="briefings-hourly-sweep",
    # Every hour on the hour. The service finds recipients whose local
    # (weekday, hour) matches now in their tz, so this single cron covers
    # every timezone.
    trigger=inngest.TriggerCron(cron="0 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=1,
)
async def briefings_hourly_sweep(ctx: inngest.Context) -> dict[str, Any]:
    """Fan-out one delivery event per due recipient."""

    due = await ctx.step.run("find-due", find_due_recipients)
    if not due:
        return {"fanned": 0}

    client = get_inngest_client()
    for item in due:
        oid = item["org_id"]
        uid = item["user_id"]
        period_key = item["period_key"]
        await ctx.step.run(
            f"enqueue-{uid}-{period_key}",
            lambda oid=oid, uid=uid, period_key=period_key: client.send(
                inngest.Event(
                    name="briefings/deliver",
                    data={
                        "org_id": oid,
                        "user_id": uid,
                        "period_key": period_key,
                        "trigger": "weekly_cron",
                    },
                )
            ),
        )

    return {"fanned": len(due)}


@_inngest_client.create_function(
    fn_id="briefings-deliver",
    trigger=inngest.TriggerEvent(event="briefings/deliver"),
    concurrency=[
        # Cap per-user — accidental fan-out on retry shouldn't double-LLM.
        inngest.Concurrency(limit=1, key="event.data.user_id", scope="fn"),
    ],
    retries=2,
    # Same-period re-fires from manual-trigger + cron should collapse.
    debounce=inngest.Debounce(
        period=timedelta(minutes=15),
        key="event.data.user_id",
    ),
)
async def briefings_deliver(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    user_id: str = data["user_id"]
    period_key: str = data.get("period_key") or current_period_key()

    result = await ctx.step.run(
        f"build-{user_id}-{period_key}",
        lambda: generate_briefing_for_user(
            org_id=org_id,
            user_id=user_id,
            period_key=period_key,
        ),
    )
    return {
        "user_id": user_id,
        "period_key": period_key,
        "created": bool(result),
    }


FUNCTIONS = [briefings_hourly_sweep, briefings_deliver]
