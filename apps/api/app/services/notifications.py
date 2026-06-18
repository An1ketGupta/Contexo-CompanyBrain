"""In-app notifications service (Migration 036).

General-purpose write/read helpers for the user notification feed. Background
functions (cron jobs, Inngest workers) call `create_notification` via the
service-role client so RLS doesn't block inserts. HTTP handlers read through
the user-scoped client so RLS scopes results to the caller.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from supabase import Client

from app.database import get_service_client

log = logging.getLogger(__name__)

# Cap the list endpoint so an admin who's never read anything doesn't load
# 10k rows. The bell popover only shows 20, and the "View all" page paginates.
DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100


async def create_notification(
    *,
    org_id: str,
    user_id: str,
    type: str,
    title: str,
    body: str | None = None,
    metadata: dict[str, Any] | None = None,
    link_url: str | None = None,
    dedupe_key: str | None = None,
    client: Client | None = None,
) -> dict[str, Any] | None:
    """Insert one notification row. Returns the new row, or None if a row
    with the same (user_id, type, dedupe_key) already exists.

    `dedupe_key` is the cron-friendly idempotency knob — pass an ISO week
    string for weekly reminders so a re-fired cron is a no-op. Pass None for
    notifications that should be allowed to repeat (e.g. real-time alerts).
    """
    svc = client or get_service_client()
    row = {
        "org_id": org_id,
        "user_id": user_id,
        "type": type,
        "title": title,
        "body": body,
        "metadata": metadata or {},
        "link_url": link_url,
        "dedupe_key": dedupe_key,
    }
    try:
        # ON CONFLICT DO NOTHING via the partial unique index. supabase-py
        # exposes this as upsert(ignore_duplicates=True). On a dup, the
        # response data is empty.
        result = await asyncio.to_thread(
            lambda: svc.table("notifications")
            .upsert(row, on_conflict="user_id,type,dedupe_key", ignore_duplicates=True)
            .execute()
        )
    except Exception as exc:
        log.warning("create_notification_failed type=%s err=%s", type, exc)
        return None

    data = result.data or []
    return data[0] if data else None


async def list_notifications(
    *,
    client: Client,
    limit: int = DEFAULT_LIST_LIMIT,
    unread_only: bool = False,
) -> list[dict[str, Any]]:
    """RLS-scoped list for the bell popover and the full list page."""
    limit = max(1, min(MAX_LIST_LIMIT, limit))

    def _run() -> list[dict[str, Any]]:
        q = (
            client.table("notifications")
            .select("id, type, title, body, metadata, link_url, read_at, created_at")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if unread_only:
            q = q.is_("read_at", "null")
        return q.execute().data or []

    return await asyncio.to_thread(_run)


async def unread_count(*, client: Client) -> int:
    """RLS-scoped unread count. Used by the bell badge."""

    def _run() -> int:
        # Postgres COUNT(*) is cheap thanks to the partial idx_notifications_user_unread.
        result = (
            client.table("notifications")
            .select("id", count="exact")
            .is_("read_at", "null")
            .limit(1)
            .execute()
        )
        return int(result.count or 0)

    return await asyncio.to_thread(_run)


async def mark_read(*, client: Client, notification_id: str) -> bool:
    """RLS-scoped: only the owner can flip their own row. Returns True if a
    row was actually updated (False if already read or not owned)."""
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    def _run() -> int:
        result = (
            client.table("notifications")
            .update({"read_at": now_iso})
            .eq("id", notification_id)
            .is_("read_at", "null")
            .execute()
        )
        return len(result.data or [])

    updated = await asyncio.to_thread(_run)
    return updated > 0


async def mark_all_read(*, client: Client) -> int:
    """Flip every unread row for the caller. RLS confines this to their own."""
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    def _run() -> int:
        result = (
            client.table("notifications")
            .update({"read_at": now_iso})
            .is_("read_at", "null")
            .execute()
        )
        return len(result.data or [])

    return await asyncio.to_thread(_run)
