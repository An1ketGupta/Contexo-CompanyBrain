"""Inngest worker for action-item reminders (#44, Agent2 Day 6).

One function — a daily cron that walks tracked items past due_date,
polls the external system for completion, and sends reminders for the
ones that are newly overdue.

Picked 09:00 UTC for the cron — early enough to land in time for US
West-coast morning. Could move to per-org timezones later but the
operational cost of a single UTC tick is far lower.
"""
from __future__ import annotations

import logging
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.services.action_tracker import check_incomplete_actions

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="action-items-daily-check",
    trigger=inngest.TriggerCron(cron="0 9 * * *"),
    retries=1,
)
async def daily_check(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    result = await step.run("check-incomplete", check_incomplete_actions)
    return result


FUNCTIONS = [daily_check]
