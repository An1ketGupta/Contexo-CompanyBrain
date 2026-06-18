"""Slack INBOUND read surface (pins, canvases, files, message context).

This module is the read counterpart to `slack.py` (which is write-only). We
keep them in separate files so each one stays focused — `slack.py` deals
with posting messages and DMs, `slack_inbound.py` deals with extracting
content FROM Slack into the knowledge base.

Surface area:
    * required_scopes() / has_inbound_scopes() — scope completeness check
    * list_bot_channels(org_id) — channels the bot is a member of (the only
      ones we can actually read from)
    * fetch_pins(org_id, channel_id) — pins.list + minimal hydration
    * fetch_message(org_id, channel_id, ts) — conversations.history filtered
      to one ts, with optional thread fetch
    * fetch_thread(org_id, channel_id, parent_ts) — conversations.replies
    * fetch_canvases(org_id, channel_id) — canvas IDs tied to a channel
    * fetch_canvas_content(org_id, canvas_id) — canvas markdown
    * fetch_files(org_id, channel_id, limit) — files.list filtered to channel
    * download_file(org_id, file) — returns bytes for the parser
    * resolve_user(org_id, user_id) — users.info with simple Redis cache

Rate limiting: Slack tiers `pins.list` at 20 calls/min, `conversations.history`
at 50/min, etc. We don't add a global limiter here — the Inngest concurrency
cap (`concurrency=1` per team_id) on the wrapping functions enforces sequential
calls, which is well under any tier limit at one workspace's worth of traffic.

The slack_sdk is intentionally not a dep; we hit the REST API directly.
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

# Scopes that the bot needs for inbound ingest in v1 (pins + canvases + files,
# explicit channel allowlist, public+private). `canvases:read` is paid-plan
# only; we treat it as best-effort (canvas ingest gracefully skips if the
# token doesn't have it).
_REQUIRED_INBOUND_SCOPES: set[str] = {
    "channels:read",
    "channels:history",
    "groups:read",
    "groups:history",
    "pins:read",
    "files:read",
    "users:read",
}

# Optional scope — having it unlocks canvas ingest. Missing it just means we
# skip canvases for that workspace; everything else still works.
CANVAS_SCOPE = "canvases:read"


def required_inbound_scopes() -> list[str]:
    return sorted(_REQUIRED_INBOUND_SCOPES)


def has_inbound_scopes(scopes: list[str] | None) -> bool:
    if not scopes:
        return False
    return _REQUIRED_INBOUND_SCOPES.issubset(set(scopes))


def has_canvas_scope(scopes: list[str] | None) -> bool:
    return bool(scopes) and CANVAS_SCOPE in set(scopes)


# ── Token lookup (shared shape with slack.py) ───────────────────────────────


async def _get_bot_token(org_id: str) -> str | None:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("bot_token, scopes")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data.get("bot_token")


async def _get_org_scopes(org_id: str) -> list[str]:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("scopes")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return []
    return list(row.data.get("scopes") or [])


# ── Low-level HTTP helper ───────────────────────────────────────────────────


async def _slack_get(
    *,
    token: str,
    path: str,
    params: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.get(
            f"{_SLACK_API}/{path}",
            params=params or {},
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 429:
        # Slack rate-limit. The Inngest layer retries with backoff; surface
        # the Retry-After so the worker can sleep precisely.
        retry_after = int(resp.headers.get("Retry-After") or "30")
        raise SlackRateLimit(retry_after=retry_after, path=path)
    if resp.status_code != 200:
        raise SlackApiError(
            f"slack_http_{resp.status_code}: {path} :: {resp.text[:200]}"
        )
    body = resp.json()
    if not body.get("ok"):
        err = body.get("error") or "unknown_error"
        raise SlackApiError(f"slack_{err}: {path}")
    return body


class SlackRateLimit(Exception):
    def __init__(self, retry_after: int, path: str) -> None:
        super().__init__(f"rate_limited:{path} retry_after={retry_after}s")
        self.retry_after = retry_after
        self.path = path


class SlackApiError(Exception):
    pass


# ── Channel listing (bot membership filter) ─────────────────────────────────


async def list_bot_channels(*, org_id: str) -> list[dict[str, Any]]:
    """Channels the bot is actually a member of.

    Different from `slack.list_channels` which returns *visible* channels —
    we want only the ones we can read from. Slack returns `is_member` in
    conversations.list so we just filter client-side rather than calling
    users.conversations (which has stricter rate limits).
    """
    token = await _get_bot_token(org_id)
    if not token:
        return []
    channels: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    while pages < 5:
        params: dict[str, str] = {
            "types": "public_channel,private_channel",
            "limit": "200",
            "exclude_archived": "true",
        }
        if cursor:
            params["cursor"] = cursor
        body = await _slack_get(token=token, path="conversations.list", params=params)
        for c in body.get("channels", []) or []:
            if not c.get("is_member"):
                continue
            channels.append(
                {
                    "id": c.get("id"),
                    "name": c.get("name"),
                    "is_private": bool(c.get("is_private")),
                    "topic": (c.get("topic") or {}).get("value"),
                }
            )
        cursor = ((body.get("response_metadata") or {}).get("next_cursor") or "").strip()
        pages += 1
        if not cursor:
            break
    channels.sort(key=lambda c: (c.get("name") or "").lower())
    return channels


async def fetch_channel_info(*, org_id: str, channel_id: str) -> dict[str, Any] | None:
    token = await _get_bot_token(org_id)
    if not token:
        return None
    try:
        body = await _slack_get(
            token=token,
            path="conversations.info",
            params={"channel": channel_id},
        )
    except SlackApiError as exc:
        log.warning("slack_channel_info_failed: %s", exc)
        return None
    ch = body.get("channel") or {}
    return {
        "id": ch.get("id"),
        "name": ch.get("name"),
        "is_private": bool(ch.get("is_private")),
        "topic": (ch.get("topic") or {}).get("value"),
        "is_member": bool(ch.get("is_member")),
    }


# ── Pins ────────────────────────────────────────────────────────────────────


async def fetch_pins(*, org_id: str, channel_id: str) -> list[dict[str, Any]]:
    """Return all pinned items in a channel.

    Each item is the raw Slack `pins.list` element, hydrated with the
    canonical message text via `conversations.history` if Slack returns
    only a stub. We filter to `type == 'message'` because Slack also lets
    files be pinned and we surface those via the files ingest path instead.
    """
    token = await _get_bot_token(org_id)
    if not token:
        return []
    body = await _slack_get(
        token=token, path="pins.list", params={"channel": channel_id}
    )
    items = body.get("items", []) or []
    out: list[dict[str, Any]] = []
    for it in items:
        if it.get("type") != "message":
            continue
        msg = it.get("message") or {}
        if not msg.get("ts"):
            continue
        out.append(
            {
                "channel_id": channel_id,
                "ts": msg["ts"],
                "user_id": msg.get("user"),
                "text": msg.get("text") or "",
                "permalink": msg.get("permalink"),
                "thread_ts": msg.get("thread_ts"),
                "created": it.get("created"),
            }
        )
    return out


async def fetch_message(
    *, org_id: str, channel_id: str, ts: str
) -> dict[str, Any] | None:
    token = await _get_bot_token(org_id)
    if not token:
        return None
    body = await _slack_get(
        token=token,
        path="conversations.history",
        params={
            "channel": channel_id,
            "latest": ts,
            "oldest": ts,
            "inclusive": "true",
            "limit": "1",
        },
    )
    messages = body.get("messages", []) or []
    if not messages:
        return None
    msg = messages[0]
    return {
        "channel_id": channel_id,
        "ts": msg.get("ts"),
        "user_id": msg.get("user"),
        "text": msg.get("text") or "",
        "thread_ts": msg.get("thread_ts"),
    }


async def fetch_thread(
    *, org_id: str, channel_id: str, parent_ts: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Return the parent + replies in a thread, oldest-first.

    Used to give a pinned message its full conversational context — a pin
    that says "approved 👍" is meaningless without the question it answers.
    We cap at 50 replies (the default Slack page) because the embed value
    plateaus past that.
    """
    token = await _get_bot_token(org_id)
    if not token:
        return []
    body = await _slack_get(
        token=token,
        path="conversations.replies",
        params={"channel": channel_id, "ts": parent_ts, "limit": str(limit)},
    )
    messages = body.get("messages", []) or []
    return [
        {
            "ts": m.get("ts"),
            "user_id": m.get("user"),
            "text": m.get("text") or "",
        }
        for m in messages
    ]


# ── Canvases ────────────────────────────────────────────────────────────────


async def fetch_channel_canvas_id(
    *, org_id: str, channel_id: str
) -> str | None:
    """Return the canvas attached to a channel, if any.

    Slack exposes the canvas via `conversations.info` under the `canvas`
    field on paid plans. We don't subscribe to canvas events directly
    (Slack's canvas event surface is undocumented in places) — instead we
    detect canvases at backfill time and re-detect on a `message.channels`
    subtype change that mentions the canvas, plus a daily refresh job.
    """
    token = await _get_bot_token(org_id)
    if not token:
        return None
    try:
        body = await _slack_get(
            token=token,
            path="conversations.info",
            params={"channel": channel_id, "include_num_members": "false"},
        )
    except SlackApiError as exc:
        log.warning("slack_conversations_info_failed: %s", exc)
        return None
    ch = body.get("channel") or {}
    properties = ch.get("properties") or {}
    canvas = properties.get("canvas") or ch.get("canvas") or {}
    file_id = canvas.get("file_id") if isinstance(canvas, dict) else None
    return file_id


async def fetch_canvas_content(*, org_id: str, canvas_file_id: str) -> str | None:
    """Return the canvas as markdown.

    Canvases are represented as files internally. `files.info` returns a
    download URL we can hit with the same bot token. We pull the canvas
    body as markdown — Slack returns text/markdown for canvas files.
    """
    token = await _get_bot_token(org_id)
    if not token:
        return None
    try:
        body = await _slack_get(
            token=token,
            path="files.info",
            params={"file": canvas_file_id},
        )
    except SlackApiError as exc:
        log.warning("slack_canvas_info_failed: %s", exc)
        return None
    file_meta = body.get("file") or {}
    url = file_meta.get("url_private_download") or file_meta.get("url_private")
    if not url:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        )
    if resp.status_code != 200:
        log.warning(
            "slack_canvas_download_failed: %s status=%s", canvas_file_id, resp.status_code
        )
        return None
    return resp.text


# ── Files ───────────────────────────────────────────────────────────────────


async def fetch_files(
    *, org_id: str, channel_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """List recent files shared into a channel.

    We cap at `limit` (default 50) on backfill to bound blast radius. For
    incremental ingest, the `file_shared` event fires per-upload.
    """
    token = await _get_bot_token(org_id)
    if not token:
        return []
    body = await _slack_get(
        token=token,
        path="files.list",
        params={"channel": channel_id, "count": str(limit)},
    )
    files = body.get("files", []) or []
    return [
        {
            "id": f.get("id"),
            "name": f.get("name") or f.get("title") or f.get("id"),
            "title": f.get("title"),
            "mimetype": f.get("mimetype"),
            "filetype": f.get("filetype"),
            "url_private": f.get("url_private"),
            "url_private_download": f.get("url_private_download"),
            "size": f.get("size"),
            "user": f.get("user"),
            "created": f.get("created"),
            "is_external": bool(f.get("is_external")),
        }
        for f in files
        if not f.get("is_external")
    ]


async def fetch_file_info(*, org_id: str, file_id: str) -> dict[str, Any] | None:
    token = await _get_bot_token(org_id)
    if not token:
        return None
    try:
        body = await _slack_get(
            token=token,
            path="files.info",
            params={"file": file_id},
        )
    except SlackApiError as exc:
        log.warning("slack_file_info_failed: %s", exc)
        return None
    return body.get("file") or None


async def download_file(*, org_id: str, file: dict[str, Any]) -> bytes | None:
    """Download a Slack file with the bot token. Returns raw bytes."""
    token = await _get_bot_token(org_id)
    if not token:
        return None
    url = file.get("url_private_download") or file.get("url_private")
    if not url:
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
        )
    if resp.status_code != 200:
        log.warning(
            "slack_file_download_failed: %s status=%s",
            file.get("id"),
            resp.status_code,
        )
        return None
    return resp.content


# ── User lookup (Redis-cached) ──────────────────────────────────────────────

_USER_CACHE_TTL = 60 * 60 * 12  # 12 hours — display names rarely change


async def resolve_user(*, org_id: str, user_id: str) -> dict[str, Any] | None:
    """Resolve a Slack U… id to {real_name, name, email} with caching.

    Used to humanize ingested message authors. Falls back to None silently
    if users:read isn't granted — the ingest path treats the author field
    as optional metadata, not load-bearing.
    """
    if not user_id:
        return None
    cache_key = f"slack:user:{org_id}:{user_id}"
    cached = await cache_get_json(cache_key)
    if isinstance(cached, dict):
        return cached
    token = await _get_bot_token(org_id)
    if not token:
        return None
    try:
        body = await _slack_get(
            token=token,
            path="users.info",
            params={"user": user_id},
        )
    except SlackApiError as exc:
        log.info("slack_users_info_skip: %s", exc)
        await cache_set_json(cache_key, {}, ttl_seconds=_USER_CACHE_TTL)
        return None
    user = body.get("user") or {}
    profile = user.get("profile") or {}
    result = {
        "id": user.get("id"),
        "name": user.get("name"),
        "real_name": profile.get("real_name") or user.get("real_name"),
        "display_name": profile.get("display_name"),
        "email": profile.get("email"),
    }
    await cache_set_json(cache_key, result, ttl_seconds=_USER_CACHE_TTL)
    return result


# ── Org ↔ team_id lookups (used by event handlers) ──────────────────────────


async def find_org_by_team(team_id: str) -> str | None:
    """Reverse-lookup an org by Slack team_id. Used by the events webhook."""
    if not team_id:
        return None
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_integrations")
        .select("org_id")
        .eq("slack_team_id", team_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data.get("org_id")


async def is_channel_subscribed(*, org_id: str, channel_id: str) -> dict[str, Any] | None:
    """Return the subscription row if this channel is on the allowlist."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("slack_channel_subscriptions")
        .select(
            "id, channel_id, channel_name, is_private, ingest_pins, "
            "ingest_canvases, ingest_files, ingest_messages"
        )
        .eq("org_id", org_id)
        .eq("channel_id", channel_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data
