"""Inngest worker for outbound Slack writes (V5 Day 3 / Agent Day 3).

Triggered by `slack/post-message` events fanned out from the slack router.
Mirrors the gmail_functions shape:
    1. Resolve the bot token (via slack.post_message internally).
    2. POST chat.postMessage.
    3. Flip messages.delivery_status to "sent" with the slack ts.

On permanent auth/permission failures (PermissionError → not_in_channel,
token_revoked, channel_not_found, is_archived) we don't retry — those need
human action (re-invite the bot, reconnect, pick a different channel).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.integrations import slack as slack_service

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


async def _mark_delivery(
    *,
    message_id: str,
    org_id: str,
    delivery: dict[str, Any],
) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({"delivery_status": delivery})
        .eq("id", message_id)
        .eq("org_id", org_id)
        .execute()
    )


@_inngest_client.create_function(
    fn_id="slack-post-message",
    trigger=inngest.TriggerEvent(event="slack/post-message"),
    retries=3,
    # Per-channel concurrency cap so one chatty channel can't starve a
    # workspace's tier-1 chat.postMessage budget (1 msg/sec/channel).
    concurrency=[inngest.Concurrency(limit=1, key="event.data.channel_id", scope="fn")],
)
async def slack_post_message(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data

    job_id: str = data["job_id"]
    message_id: str = data["message_id"]
    org_id: str = data["org_id"]
    user_id: str | None = data.get("user_id")
    conversation_id: str | None = data.get("conversation_id")
    channel_id: str = data["channel_id"]
    channel_name: str = data.get("channel_name") or channel_id
    text: str = data["text"]
    thread_ts: str | None = data.get("thread_ts")
    destination = f"#{channel_name}"

    from app.services.outbound_postwrite import on_failed, on_sent

    try:
        result = await step.run(
            "post-to-channel",
            lambda: slack_service.post_message(
                org_id=org_id,
                channel_id=channel_id,
                text=text,
                thread_ts=thread_ts,
            ),
        )
    except PermissionError as exc:
        reason = str(exc) or "slack_post_unauthorized"
        await _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "slack",
                "status": "failed",
                "channel_name": channel_name,
                "channel_id": channel_id,
                "job_id": job_id,
                "error": reason,
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        # PermissionError is terminal — no retry budget will fix a revoked
        # token or a bot kicked out of a channel.
        await on_failed(
            run_id=job_id,
            channel="slack",
            org_id=org_id,
            user_id=user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            destination=destination,
            reason=reason,
            permanent=True,
        )
        return {"status": "failed", "reason": reason}

    await step.run(
        "record-delivery",
        lambda: _mark_delivery(
            message_id=message_id,
            org_id=org_id,
            delivery={
                "channel": "slack",
                "status": "sent",
                "channel_name": channel_name,
                "channel_id": channel_id,
                "job_id": job_id,
                "external_id": result.get("ts"),
                "thread_ts": thread_ts,
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            },
        ),
    )

    # Slack chat.postMessage doesn't return a clickable URL, but we can build
    # one from the team + channel + ts. We only know the channel; the team
    # comes from the integration row. Best-effort: no URL when unresolvable.
    await on_sent(
        run_id=job_id,
        channel="slack",
        org_id=org_id,
        message_id=message_id,
        destination=destination,
        external_id=result.get("ts"),
        url=None,
        extra={"channel_id": channel_id, "thread_ts": thread_ts},
    )

    return {"status": "sent", "slack_ts": result.get("ts")}


FUNCTIONS = [slack_post_message]
