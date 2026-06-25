"""Inngest workers for output quality scoring (#52, Agent2 Day 6).

Two functions:

  quality-score-message      (event: quality/score.message)
      Recompute the score for one message. Fired from:
        * the chat pipeline after assistant message creation
        * the feedback router after thumbs up/down
        * the copy-tracking endpoint

  quality-threshold-alerts   (daily cron 09:30 UTC)
      Walk every org with a quality_alert_threshold, surface this week's
      avg, drop a notification to admins if below threshold.
"""
from __future__ import annotations

import logging
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.services.quality_scoring import check_threshold_alerts, score_message

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="quality-score-message",
    trigger=inngest.TriggerEvent(event="quality/score.message"),
    concurrency=[
        # Several signals can fire for the same message in close succession
        # (copy + feedback within seconds). Serialize by message_id so we
        # don't race the upsert.
        inngest.Concurrency(limit=1, key="event.data.message_id", scope="fn"),
    ],
    retries=2,
)
async def score_message_fn(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    message_id = ctx.event.data["message_id"]
    result = await step.run(
        f"score-{message_id}",
        lambda: score_message(message_id=message_id),
    )
    if not result:
        return {"message_id": message_id, "status": "skipped"}
    return {"message_id": message_id, "score": result.get("score")}


@_inngest_client.create_function(
    fn_id="quality-threshold-alerts",
    trigger=inngest.TriggerCron(cron="30 9 * * *"),
    retries=1,
)
async def threshold_alerts(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    return await step.run("check-thresholds", check_threshold_alerts)


FUNCTIONS = [score_message_fn, threshold_alerts]
