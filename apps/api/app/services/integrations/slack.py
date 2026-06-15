"""Slack write surface: post_message + list_channels.

OAuth, signing-secret verification, and the slash-command/interactivity
handlers live in `app.routers.slack_router`. This module is the network
adapter for outbound Slack writes — kept separate so the router stays
focused on HTTP semantics and this module stays focused on the Slack API.

We talk to Slack's REST API directly (no slack_sdk dep) — mirrors the
slack_router pattern and keeps the FastAPI image lean.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.database import get_service_client
from app.services.redis_cache import cache_get_json, cache_set_json

log = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"
# Channel lists are cached for 1h — long enough that a chat session sees
# zero round-trips per click, short enough that a new channel surfaces
# before users notice. Bumped manually by an org admin would be overkill;
# the picker has a "Refresh" affordance that calls cache_invalidate.
_CHANNELS_CACHE_TTL = 60 * 60


async def get_bot_token(org_id: str) -> str | None:
    """Fetch the workspace bot token for an org. Returns None if not connected."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("bot_token")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data.get("bot_token")


async def list_channels(*, org_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Returns all public + private channels the bot has access to.

    Cached in Upstash for 1h per-org. Slack's tier-2 conversations.list cap
    is ~20 calls/minute — without a cache, an org with many users opening
    the channel picker concurrently would burn through that quickly.

    Pagination: we walk up to 5 pages (1000 channels) and stop. Anyone with
    more channels than that is an enterprise-scale workspace where the
    channel picker as a dropdown isn't the right UX anyway — they'd want
    a search-backed combobox, which is a follow-up.
    """
    cache_key = f"slack:channels:{org_id}"
    if not force_refresh:
        cached = await cache_get_json(cache_key)
        if isinstance(cached, list):
            return cached

    token = await get_bot_token(org_id=org_id)
    if not token:
        return []

    channels: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        while pages < 5:
            params: dict[str, str] = {
                "types": "public_channel,private_channel",
                "limit": "200",
                "exclude_archived": "true",
            }
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(
                f"{_SLACK_API}/conversations.list",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            payload = resp.json() if resp.status_code == 200 else {}
            if resp.status_code != 200 or not payload.get("ok"):
                log.warning(
                    "slack_list_channels_failed",
                    status=resp.status_code,
                    error=(payload or {}).get("error"),
                )
                break
            for c in payload.get("channels", []) or []:
                channels.append(
                    {
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "is_private": bool(c.get("is_private")),
                        "is_member": bool(c.get("is_member")),
                    }
                )
            cursor = ((payload.get("response_metadata") or {}).get("next_cursor") or "").strip()
            pages += 1
            if not cursor:
                break

    # Sort alphabetically for a stable picker. We surface is_member but don't
    # filter on it — Slack will return "not_in_channel" if the bot tries to
    # post to a channel it isn't a member of, and the worker surfaces that
    # back to the user as a clear actionable error rather than us guessing.
    channels.sort(key=lambda c: (c.get("name") or "").lower())

    await cache_set_json(cache_key, channels, ttl_seconds=_CHANNELS_CACHE_TTL)
    return channels


async def post_message(
    *,
    org_id: str,
    channel_id: str,
    text: str,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """POST to https://slack.com/api/chat.postMessage.

    Returns {ts, channel}. Raises:
        - PermissionError on token_revoked / not_in_channel / channel_not_found
          (UI converts to a re-invite-the-bot prompt without a retry).
        - RuntimeError on transient failures (Inngest retries).

    We don't use Block Kit here — that lives in `slack_block_kit.py` for the
    /brain card flows. The chat "post to Slack" flow is plain mrkdwn so the
    output reads like a teammate wrote it (no big "this is an AI message"
    chrome).
    """
    token = await get_bot_token(org_id=org_id)
    if not token:
        raise PermissionError("slack_not_connected")

    payload: dict[str, Any] = {
        "channel": channel_id,
        "text": text,
        "mrkdwn": True,
        # Disable link unfurls by default; outbound AI text rarely benefits
        # from a third-party preview and unfurl previews are noisy.
        "unfurl_links": False,
        "unfurl_media": False,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_SLACK_API}/chat.postMessage",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    body = resp.json() if resp.status_code == 200 else {}
    if resp.status_code == 200 and body.get("ok"):
        return {"ts": body.get("ts"), "channel": body.get("channel")}

    err = (body or {}).get("error") or f"http_{resp.status_code}"
    # Map the most common Slack-side errors to PermissionError so the Inngest
    # worker doesn't burn retry budget on issues that can never succeed
    # without user intervention.
    if err in {"token_revoked", "invalid_auth", "account_inactive"}:
        raise PermissionError("slack_token_revoked")
    if err in {"not_in_channel", "channel_not_found", "is_archived"}:
        raise PermissionError(f"slack_{err}")
    raise RuntimeError(f"slack_post_failed: {err}")


async def invalidate_channels_cache(org_id: str) -> None:
    """Drop the cached channel list — used after the user clicks "Refresh"
    in the picker or when the bot is re-installed (new bot_user_id)."""
    from app.services.redis_cache import cache_delete

    await cache_delete(f"slack:channels:{org_id}")


# ── Direct messages (Agent Roadmap Day 6: approval DMs, Day 8: onboarding) ──


async def lookup_user_id_by_email(*, org_id: str, email: str) -> str | None:
    """Slack users.lookupByEmail — returns the Slack user id (Uxxx) or None.

    Required for chat.postMessage to a DM: we open a DM channel by user id
    via conversations.open, then post into the resulting channel id.

    Returns None on any failure (token missing, user_not_found, permissions).
    The caller should treat that as "user isn't on Slack" and skip the DM
    rather than fail the workflow.
    """
    token = await get_bot_token(org_id=org_id)
    if not token or not email:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(
            f"{_SLACK_API}/users.lookupByEmail",
            params={"email": email},
            headers={"Authorization": f"Bearer {token}"},
        )
    body = resp.json() if resp.status_code == 200 else {}
    if resp.status_code == 200 and body.get("ok"):
        return ((body.get("user") or {}).get("id")) or None
    err = (body or {}).get("error") or f"http_{resp.status_code}"
    if err not in {"users_not_found"}:
        log.warning("slack_lookup_email_failed", email=email, error=err)
    return None


async def send_dm(
    *,
    org_id: str,
    user_email: str | None = None,
    user_id: str | None = None,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Send a DM. Either `user_email` or `user_id` must be provided.

    Returns {ts, channel} on success, None when the user couldn't be found
    on the Slack workspace (treated as a soft skip).

    We do two API calls — users.lookupByEmail (if needed) and conversations.open
    — then a chat.postMessage. The open step is idempotent on Slack's side
    so retries are safe.
    """
    token = await get_bot_token(org_id=org_id)
    if not token:
        raise PermissionError("slack_not_connected")

    resolved_user_id = user_id
    if not resolved_user_id and user_email:
        resolved_user_id = await lookup_user_id_by_email(org_id=org_id, email=user_email)
    if not resolved_user_id:
        log.info("slack_dm_skipped_user_not_found", email=user_email)
        return None

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        # 1. Open a DM channel.
        open_resp = await client.post(
            f"{_SLACK_API}/conversations.open",
            json={"users": resolved_user_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        open_body = open_resp.json() if open_resp.status_code == 200 else {}
        if open_resp.status_code != 200 or not open_body.get("ok"):
            err = (open_body or {}).get("error") or f"http_{open_resp.status_code}"
            if err in {"token_revoked", "invalid_auth", "account_inactive"}:
                raise PermissionError("slack_token_revoked")
            raise RuntimeError(f"slack_open_dm_failed: {err}")
        dm_channel = ((open_body.get("channel") or {}).get("id")) or resolved_user_id

        # 2. Post into the DM channel.
        payload: dict[str, Any] = {
            "channel": dm_channel,
            "text": text,
            "mrkdwn": True,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks:
            payload["blocks"] = blocks
        post_resp = await client.post(
            f"{_SLACK_API}/chat.postMessage",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

    body = post_resp.json() if post_resp.status_code == 200 else {}
    if post_resp.status_code == 200 and body.get("ok"):
        return {"ts": body.get("ts"), "channel": body.get("channel")}

    err = (body or {}).get("error") or f"http_{post_resp.status_code}"
    if err in {"token_revoked", "invalid_auth", "account_inactive"}:
        raise PermissionError("slack_token_revoked")
    raise RuntimeError(f"slack_dm_post_failed: {err}")


async def update_message(
    *,
    org_id: str,
    channel: str,
    ts: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
) -> None:
    """chat.update — replace the contents of an existing Slack message.

    Used to mutate the approval DM in place once the buttons are clicked
    (so the recipient doesn't see stale Approve/Reject buttons on a
    decision that's already been made).
    """
    token = await get_bot_token(org_id=org_id)
    if not token:
        return
    payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_SLACK_API}/chat.update",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
    body = resp.json() if resp.status_code == 200 else {}
    if resp.status_code != 200 or not body.get("ok"):
        log.warning(
            "slack_update_failed",
            channel=channel,
            ts=ts,
            error=(body or {}).get("error") or f"http_{resp.status_code}",
        )
