"""Fire-and-forget product event logger.

`track_event` is called from request handlers and Inngest jobs to record
*what happened* (a chat sent, a doc uploaded, a template used). Reads power
the admin analytics dashboard (`GET /admin/analytics`).

Three invariants:
  * Never raises. A failing insert may not break the user-facing flow.
  * Writes via the service role client — bypasses RLS, which has no INSERT
    policy for `analytics_events`. (Reads are RLS-scoped on the user client.)
  * Runs sync DB calls via `asyncio.to_thread` so the async handler isn't
    blocked. We do NOT spawn a fire-and-forget Task — that hides errors
    AND can be GC'd mid-write. The 5–20ms cost is acceptable; if it ever
    isn't, we move to a queued Inngest event.

Event-type taxonomy (keep this list in sync with the analytics router):
  chat_sent          metadata: { intent, query_len, source_count }
  doc_uploaded       metadata: { file_type, file_size_kb }
  doc_deleted        metadata: { file_type }
  feedback_given     metadata: { feedback: 'positive'|'negative' }
  invite_sent        metadata: { role }
  invite_accepted    metadata: { }
  template_used      metadata: { template_id, category }
  share_created      metadata: { }
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)


async def track_event(
    *,
    org_id: str,
    event_type: str,
    user_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort event log. Swallows every exception."""
    if not org_id or not event_type:
        return
    try:
        svc = get_service_client()
        row: dict[str, Any] = {
            "org_id": org_id,
            "event_type": event_type,
            "metadata": metadata or {},
        }
        if user_id:
            row["user_id"] = user_id
        await asyncio.to_thread(
            lambda: svc.table("analytics_events").insert(row).execute()
        )
    except Exception as exc:
        # Telemetry. Drop it on the floor — don't fail the caller.
        log.warning("track_event failed event_type=%s err=%s", event_type, exc)
