"""Meeting Inngest pipeline.

Two functions live here:
  * `process_meeting_transcript_fn` — Agent Day 13. Wraps MeetingNotesAgent
    after a transcript document finishes ingestion.
  * `meeting_prep_created_fn` — fires when a user submits the Meeting Prep
    form. The brief itself streams through /chat/stream (better UX than
    a background job for a one-shot generation), so this handler only does
    the analytics rollup and is the seam for future post-processing
    (calendar attach, reminder schedule, follow-up suggestions).
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.meeting_notes_agent import MeetingNotesAgent
from app.services.analytics import track_event

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


@_inngest_client.create_function(
    fn_id="meeting-prep-created",
    trigger=inngest.TriggerEvent(event="meeting-prep/created"),
    retries=1,
)
async def meeting_prep_created_fn(ctx: inngest.Context) -> dict[str, Any]:
    """Telemetry sink for Meeting Prep submissions.

    Today: log a `meeting_prep_created` analytics event so the admin
    dashboard can break down briefs by meeting type.

    Tomorrow: this is the place to wire follow-up automation (e.g. attach
    the brief to a calendar invite, schedule a reminder, fire an
    `agent/meeting-followup` event) without touching the request hot path.
    """
    data = ctx.event.data
    org_id = data.get("org_id")
    user_id = data.get("user_id")
    conversation_id = data.get("conversation_id")
    meeting_type = data.get("meeting_type")

    if not org_id or not conversation_id:
        return {"status": "skipped", "reason": "missing_required_fields"}

    await track_event(
        org_id=org_id,
        user_id=user_id,
        event_type="meeting_prep_created",
        metadata={
            "conversation_id": conversation_id,
            "meeting_type": meeting_type,
            "attendees_chars": data.get("attendees_chars"),
            "topics_chars": data.get("topics_chars"),
            "has_date": data.get("has_date", False),
            "has_additional_context": data.get("has_additional_context", False),
        },
    )
    return {"status": "ok"}


FUNCTIONS = [
    process_meeting_transcript_fn,
    meeting_prep_created_fn,
]
