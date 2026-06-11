"""Team-visible activity feed writer (V4 #57).

Distinct from `services/analytics.py`:
  * Activity rows are user-facing and rendered as-is in the team feed.
  * They respect `users.activity_private` (resolved at WRITE time — see the
    migration comment on `activity_feed.is_private`).
  * Only four narrow activity types — the feed is "what people did", not
    "every event". Generic counters live in `analytics_events`.

Like `track_event`, this is fire-and-forget: an insert failure here must
not break the user-facing call.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from app.database import get_service_client

log = logging.getLogger(__name__)


# Each entry is a callable that renders the row's `summary`. Strings are kept
# short and friendly — they're rendered in a sidebar list, not a notification.
ACTIVITY_TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    "generated_content": lambda m: (
        f"Generated {m.get('intent_label', 'content')}"
    ),
    "uploaded_doc": lambda m: f"Uploaded \"{m.get('doc_name', 'a document')}\"",
    "shared_output": lambda m: "Shared an output",
    "used_template": lambda m: (
        f"Used the \"{m.get('template_title', 'a template')}\" template"
    ),
}


# Intent → friendly verb for the feed. Keep aligned with QueryIntent enum.
INTENT_FEED_LABEL = {
    "task_generation": "an output",
    "analysis": "an analysis",
    "factual_qa": "a Q&A response",
    "search": "a search result",
}


async def log_activity(
    *,
    org_id: str,
    user_id: str,
    activity_type: str,
    metadata: dict[str, Any] | None = None,
    is_private: bool = False,
) -> None:
    """Insert one activity row. Never raises.

    `is_private` should be set by the caller from the user's current
    `activity_private` preference. We do the lookup here only if the caller
    didn't pre-resolve it (cheaper for the caller to pass it through from a
    user object already in scope).
    """
    if not org_id or not user_id or activity_type not in ACTIVITY_TEMPLATES:
        return

    metadata = metadata or {}
    summary_fn = ACTIVITY_TEMPLATES[activity_type]
    summary = summary_fn(metadata)
    # Hard-cap at the column constraint length — defense in depth.
    if len(summary) > 280:
        summary = summary[:277] + "..."

    row = {
        "org_id": org_id,
        "user_id": user_id,
        "activity_type": activity_type,
        "summary": summary,
        "metadata": metadata,
        "is_private": bool(is_private),
    }

    try:
        svc = get_service_client()
        await asyncio.to_thread(
            lambda: svc.table("activity_feed").insert(row).execute()
        )
    except Exception as exc:
        log.warning("log_activity failed type=%s err=%s", activity_type, exc)


async def resolve_user_privacy(user_id: str) -> bool:
    """Read users.activity_private for one user. Returns False on lookup error
    so we fail OPEN (visible) rather than fail CLOSED — a user who hasn't set
    the preference shouldn't suddenly hide their activity because a query
    failed. The toggle is opt-IN to privacy."""
    try:
        svc = get_service_client()
        res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("activity_private")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        return bool(res and res.data and res.data.get("activity_private"))
    except Exception:
        return False
