"""Inngest functions for Slack INBOUND (pins / canvases / files → docs).

Fan-out shape:

    Slack Events API webhook --> slack/process-event ┬─> slack/ingest-pin
                                                     ├─> slack/ingest-canvas
                                                     └─> slack/ingest-file

Backfill (one-shot, fired when an admin subscribes a new channel):

    POST /integrations/slack/subscriptions --> slack/backfill-channel
        which fans out pin/canvas/file ingest events for everything in scope

Concurrency:
    `concurrency=1` per `team_id` on every fn so Slack's tier-2/tier-3 limits
    are respected without a separate rate limiter. Slack's `Retry-After`
    header is handled by raising SlackRateLimit from the wrapper layer; the
    Inngest retry policy backs off automatically.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.integrations import slack_inbound, slack_ingest

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()

# Cap on how many recent files we backfill per channel — a chatty channel
# can have thousands. We grab the most-recent 50, which covers the "recent
# tribal knowledge" use case without an embed-bill blowout.
_BACKFILL_FILE_LIMIT = 50


# ── 1. process-event (fan-out from /slack/events webhook) ──────────────────


@_inngest_client.create_function(
    fn_id="slack-process-event",
    trigger=inngest.TriggerEvent(event="slack/inbound-event"),
    retries=3,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.team_id", scope="fn")
    ],
)
async def slack_process_event(ctx: inngest.Context) -> dict[str, Any]:
    """Route a Slack event to the right ingest function.

    The router POSTs a normalised envelope to this function so we can scale
    out per-team without coupling event parsing to the HTTP handler. Events
    we route:

      * pin_added / pin_removed
      * message (subtype-aware: handles edits to pinned messages)
      * file_shared / file_unshared
      * canvas_added / canvas_changed
      * member_joined_channel  (bot is the joiner → enable channel auto-backfill?)
    """
    data = ctx.event.data
    event_type: str = data["event_type"]
    org_id: str = data["org_id"]
    team_id: str = data["team_id"]
    payload: dict[str, Any] = data.get("payload") or {}

    if event_type == "pin_added":
        item = payload.get("item") or {}
        if item.get("type") != "message":
            return {"status": "skipped", "reason": "non_message_pin"}
        channel_id = item.get("channel") or payload.get("channel_id")
        msg = item.get("message") or {}
        ts = msg.get("ts")
        if not channel_id or not ts:
            return {"status": "skipped", "reason": "missing_ids"}

        sub = await slack_inbound.is_channel_subscribed(
            org_id=org_id, channel_id=channel_id
        )
        if not sub or not sub.get("ingest_pins"):
            return {"status": "skipped", "reason": "not_subscribed"}

        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="slack/ingest-pin",
                data={
                    "org_id": org_id,
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "channel_name": sub.get("channel_name"),
                    "ts": ts,
                },
                id=f"slack-pin-{org_id}-{channel_id}-{ts}",
            )
        )
        return {"status": "queued", "type": "pin"}

    if event_type == "pin_removed":
        item = payload.get("item") or {}
        channel_id = item.get("channel") or payload.get("channel_id")
        ts = (item.get("message") or {}).get("ts")
        if not channel_id or not ts:
            return {"status": "skipped", "reason": "missing_ids"}
        return await slack_ingest.remove_pin_document(
            org_id=org_id, team_id=team_id, channel_id=channel_id, ts=ts
        )

    if event_type == "file_shared":
        # file_shared fires for every file uploaded to a channel the bot
        # can see. We re-validate subscription + ingest_files toggle, then
        # fan out a single ingest event.
        file_id = (payload.get("file_id")
                   or (payload.get("file") or {}).get("id"))
        channel_id = payload.get("channel_id") or payload.get("channel")
        if not file_id or not channel_id:
            return {"status": "skipped", "reason": "missing_ids"}
        sub = await slack_inbound.is_channel_subscribed(
            org_id=org_id, channel_id=channel_id
        )
        if not sub or not sub.get("ingest_files"):
            return {"status": "skipped", "reason": "not_subscribed"}

        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="slack/ingest-file",
                data={
                    "org_id": org_id,
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "channel_name": sub.get("channel_name"),
                    "file_id": file_id,
                },
                id=f"slack-file-shared-{org_id}-{file_id}",
            )
        )
        return {"status": "queued", "type": "file"}

    if event_type == "message":
        # Two cases worth acting on:
        #   1. subtype == 'pinned_item' — the system message Slack posts when
        #      someone pins. We don't need to act because pin_added covers it.
        #   2. subtype == 'message_changed' on a previously-pinned message —
        #      re-ingest if the edited message is in the current pin list.
        subtype = payload.get("subtype")
        channel_id = payload.get("channel")
        if subtype == "message_changed" and channel_id:
            edited = payload.get("message") or {}
            ts = edited.get("ts")
            if not ts:
                return {"status": "skipped", "reason": "no_ts"}
            # Cheap heuristic: re-ingest if this ts already lives as a pin
            # document for the org. Avoids hammering Slack on every edit.
            svc = get_service_client()
            ext_id = slack_ingest.pin_external_id(team_id, channel_id, ts)
            existing = await asyncio.to_thread(
                lambda: svc.table("documents")
                .select("id")
                .eq("org_id", org_id)
                .eq("source", slack_ingest.SOURCE_TAG)
                .eq("external_id", ext_id)
                .maybe_single()
                .execute()
            )
            if not existing or not existing.data:
                return {"status": "skipped", "reason": "not_tracked"}
            sub = await slack_inbound.is_channel_subscribed(
                org_id=org_id, channel_id=channel_id
            )
            if not sub:
                return {"status": "skipped", "reason": "not_subscribed"}
            client = get_inngest_client()
            await client.send(
                inngest.Event(
                    name="slack/ingest-pin",
                    data={
                        "org_id": org_id,
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "channel_name": sub.get("channel_name"),
                        "ts": ts,
                    },
                    id=f"slack-pin-edit-{org_id}-{channel_id}-{ts}-"
                        f"{edited.get('edited', {}).get('ts', '')}",
                )
            )
            return {"status": "queued", "type": "pin_edit"}
        return {"status": "skipped", "reason": "ignored_message_subtype"}

    if event_type in ("canvas_changed", "canvas_added"):
        # Slack's canvas event surface is still evolving. We treat both
        # events identically: re-ingest the canvas tied to the channel.
        channel_id = payload.get("channel_id") or payload.get("channel")
        if not channel_id:
            return {"status": "skipped", "reason": "no_channel"}
        sub = await slack_inbound.is_channel_subscribed(
            org_id=org_id, channel_id=channel_id
        )
        if not sub or not sub.get("ingest_canvases"):
            return {"status": "skipped", "reason": "not_subscribed"}
        canvas_id = await slack_inbound.fetch_channel_canvas_id(
            org_id=org_id, channel_id=channel_id
        )
        if not canvas_id:
            return {"status": "skipped", "reason": "no_canvas"}
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="slack/ingest-canvas",
                data={
                    "org_id": org_id,
                    "team_id": team_id,
                    "channel_id": channel_id,
                    "channel_name": sub.get("channel_name"),
                    "canvas_id": canvas_id,
                },
                id=f"slack-canvas-{org_id}-{canvas_id}",
            )
        )
        return {"status": "queued", "type": "canvas"}

    if event_type == "member_joined_channel":
        # The bot itself joined — surface in logs so an admin sees the
        # connection in the events feed. We don't auto-backfill: subscription
        # creation is admin-driven.
        log.info(
            "slack_bot_joined channel=%s team=%s",
            payload.get("channel"),
            team_id,
        )
        return {"status": "noted"}

    log.info("slack_event_unhandled type=%s", event_type)
    return {"status": "ignored", "type": event_type}


# ── 2. backfill-channel (one-shot on subscription) ──────────────────────────


@_inngest_client.create_function(
    fn_id="slack-backfill-channel",
    trigger=inngest.TriggerEvent(event="slack/backfill-channel"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.team_id", scope="fn")
    ],
)
async def slack_backfill_channel(ctx: inngest.Context) -> dict[str, Any]:
    """Initial pull: pins + canvases + recent files for a newly-subscribed channel.

    Each item is fanned out as an individual ingest event so a single failing
    file doesn't take down the rest of the channel's content. We stamp
    `last_backfilled_at` at the end for the UI's "last synced" display.
    """
    step = ctx.step
    data = ctx.event.data
    org_id: str = data["org_id"]
    team_id: str = data["team_id"]
    channel_id: str = data["channel_id"]
    channel_name: str | None = data.get("channel_name")

    sub = await slack_inbound.is_channel_subscribed(
        org_id=org_id, channel_id=channel_id
    )
    if not sub:
        return {"status": "skipped", "reason": "subscription_removed"}

    pins_queued = 0
    canvases_queued = 0
    files_queued = 0

    client = get_inngest_client()

    if sub.get("ingest_pins"):
        pins = await step.run(
            "fetch-pins",
            lambda: slack_inbound.fetch_pins(org_id=org_id, channel_id=channel_id),
        )
        for p in pins:
            await client.send(
                inngest.Event(
                    name="slack/ingest-pin",
                    data={
                        "org_id": org_id,
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "ts": p["ts"],
                    },
                    id=f"slack-pin-{org_id}-{channel_id}-{p['ts']}-backfill",
                )
            )
            pins_queued += 1

    if sub.get("ingest_canvases"):
        canvas_id = await step.run(
            "fetch-canvas-id",
            lambda: slack_inbound.fetch_channel_canvas_id(
                org_id=org_id, channel_id=channel_id
            ),
        )
        if canvas_id:
            await client.send(
                inngest.Event(
                    name="slack/ingest-canvas",
                    data={
                        "org_id": org_id,
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "canvas_id": canvas_id,
                    },
                    id=f"slack-canvas-{org_id}-{canvas_id}-backfill",
                )
            )
            canvases_queued = 1

    if sub.get("ingest_files"):
        files = await step.run(
            "fetch-files",
            lambda: slack_inbound.fetch_files(
                org_id=org_id, channel_id=channel_id, limit=_BACKFILL_FILE_LIMIT
            ),
        )
        for f in files:
            await client.send(
                inngest.Event(
                    name="slack/ingest-file",
                    data={
                        "org_id": org_id,
                        "team_id": team_id,
                        "channel_id": channel_id,
                        "channel_name": channel_name,
                        "file_id": f["id"],
                    },
                    id=f"slack-file-{org_id}-{f['id']}-backfill",
                )
            )
            files_queued += 1

    # Stamp backfill completion.
    svc = get_service_client()
    await step.run(
        "stamp-backfilled",
        lambda: _stamp_backfilled(
            svc=svc,
            org_id=org_id,
            channel_id=channel_id,
            stamp=datetime.now(timezone.utc).isoformat(),
        ),
    )

    return {
        "status": "ok",
        "pins": pins_queued,
        "canvases": canvases_queued,
        "files": files_queued,
    }


def _stamp_backfilled(*, svc, org_id: str, channel_id: str, stamp: str) -> None:
    svc.table("slack_channel_subscriptions").update(
        {"last_backfilled_at": stamp, "last_error": None, "last_error_at": None}
    ).eq("org_id", org_id).eq("channel_id", channel_id).execute()


# ── 3. ingest-pin ───────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="slack-ingest-pin",
    trigger=inngest.TriggerEvent(event="slack/ingest-pin"),
    retries=3,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.team_id", scope="fn")
    ],
)
async def slack_ingest_pin_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    try:
        return await slack_ingest.ingest_pin(
            org_id=data["org_id"],
            team_id=data["team_id"],
            channel_id=data["channel_id"],
            channel_name=data.get("channel_name"),
            ts=data["ts"],
        )
    except slack_inbound.SlackRateLimit as exc:
        # Surface the precise Retry-After in the error; Inngest retries with
        # backoff so this just informs the user when looking at the run log.
        log.warning("slack_pin_rate_limited retry_after=%s", exc.retry_after)
        raise


# ── 4. ingest-canvas ────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="slack-ingest-canvas",
    trigger=inngest.TriggerEvent(event="slack/ingest-canvas"),
    retries=3,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.team_id", scope="fn")
    ],
)
async def slack_ingest_canvas_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    return await slack_ingest.ingest_canvas(
        org_id=data["org_id"],
        team_id=data["team_id"],
        channel_id=data.get("channel_id"),
        channel_name=data.get("channel_name"),
        canvas_id=data["canvas_id"],
    )


# ── 5. ingest-file ──────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="slack-ingest-file",
    trigger=inngest.TriggerEvent(event="slack/ingest-file"),
    retries=3,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.team_id", scope="fn")
    ],
)
async def slack_ingest_file_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    file_meta = await slack_inbound.fetch_file_info(
        org_id=data["org_id"], file_id=data["file_id"]
    )
    if not file_meta:
        return {"status": "skipped", "reason": "file_lookup_failed"}
    return await slack_ingest.ingest_file(
        org_id=data["org_id"],
        team_id=data["team_id"],
        channel_id=data.get("channel_id"),
        channel_name=data.get("channel_name"),
        file=file_meta,
    )


FUNCTIONS = [
    slack_process_event,
    slack_backfill_channel,
    slack_ingest_pin_fn,
    slack_ingest_canvas_fn,
    slack_ingest_file_fn,
]
