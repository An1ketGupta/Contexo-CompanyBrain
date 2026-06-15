"""Meeting-notes Inngest pipeline (Agent Day 13).

Fires after a meeting transcript document finishes the regular doc
pipeline. Wraps MeetingNotesAgent.run_safely() so the extraction work runs
asynchronously after the source doc is queryable.

Concurrency cap: 1 per (org_id, document_id) so a fast double-upload of
the same transcript can't double-post action items to Slack.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.meeting_notes_agent import MeetingNotesAgent

log = get_logger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="process-meeting-transcript",
    trigger=inngest.TriggerEvent(event="meeting/transcript-uploaded"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.doc_id", scope="fn"),
    ],
)
async def process_meeting_transcript_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    doc_id = data.get("doc_id")
    org_id = data.get("org_id")
    file_type = data.get("file_type") or "vtt"

    if not doc_id or not org_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    agent = MeetingNotesAgent(
        org_id=org_id, document_id=doc_id, file_type=file_type,
    )
    try:
        result = await agent.run_safely()
    except Exception as exc:
        log.warning(
            "meeting_agent_run_failed",
            org_id=org_id, doc_id=doc_id, error=str(exc),
        )
        raise
    return {"status": "ok", "run_id": agent.run_id, **(result or {})}


FUNCTIONS = [process_meeting_transcript_fn]
