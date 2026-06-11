"""Meeting Prep Assistant — V4 #39.

Surfaces a guided form (meeting type, attendees, topics, date, extra context)
and converts it into a structured prompt the existing chat pipeline can run.

Design: we DON'T duck-tape a `system_prompt_override` into execute_task.
Two reasons:
  * The intent overlay system in task_chain already does per-mode prompting;
    bolting on another path is a maintenance trap.
  * The 6-section structure the user wants is reliably produced by *the query
    itself* if the query lists the sections.

So this endpoint is a pure translator:
  POST /chat/meeting-prep  → creates a conversation, returns
        { conversation_id, prompt }
  Frontend then sends `prompt` through the normal /chat/stream — same SSE,
  same citation tracking, same analytics, zero code duplication.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.database import get_user_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat", "meeting-prep"])


MeetingType = Literal[
    "Investor Meeting",
    "Client Pitch",
    "Team All-Hands",
    "One-on-One",
    "Board Meeting",
    "Sales Call",
    "Performance Review",
    "Project Kickoff",
    "Other",
]


class MeetingPrepBody(BaseModel):
    meeting_type: MeetingType
    attendees: str = Field(..., min_length=1, max_length=600)
    topics: str = Field(..., min_length=1, max_length=2_000)
    # YYYY-MM-DD or empty.
    date: str | None = Field(default=None, max_length=10)
    additional_context: str | None = Field(default=None, max_length=2_000)

    @field_validator("attendees", "topics")
    @classmethod
    def _strip_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Cannot be empty.")
        return v

    @field_validator("date")
    @classmethod
    def _strip_optional_date(cls, v: str | None) -> str | None:
        return (v or "").strip() or None


# Single source of truth for the brief structure. Editing here ripples to
# every prep through the normal chat pipeline.
_BRIEF_SECTIONS = [
    "Meeting Overview",
    "Background on Attendees / Organisation",
    "Key Talking Points",
    "Anticipated Questions & Suggested Answers",
    "Relevant Numbers & Metrics",
    "Recommended Next Steps",
]


def _build_query(body: MeetingPrepBody) -> str:
    sections_md = "\n".join(f"## {s}" for s in _BRIEF_SECTIONS)
    extra = (
        f"\nAdditional context: {body.additional_context}\n"
        if body.additional_context
        else ""
    )
    when = f"\nDate: {body.date}" if body.date else ""
    return (
        f"Prepare a structured meeting brief grounded in our knowledge base.\n\n"
        f"Meeting type: {body.meeting_type}{when}\n"
        f"Attendees: {body.attendees}\n"
        f"Key topics: {body.topics}\n"
        f"{extra}\n"
        f"Search the knowledge base for context on the attendees, their "
        f"company (if applicable), the topics, and our prior position. Then "
        f"return the brief using exactly these sections (Markdown H2 headers, "
        f"in order):\n{sections_md}\n\n"
        f"Be specific. Cite the documents you draw from. If a section has no "
        f"supporting context in the knowledge base, say so in one line rather "
        f"than padding."
    )


@router.post("/meeting-prep", status_code=status.HTTP_201_CREATED)
async def create_meeting_prep(
    body: MeetingPrepBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Create a fresh conversation seeded with a meeting prep query.

    Returns { conversation_id, prompt }. The browser navigates to
    /chat/{conversation_id} and POSTs `prompt` to /api/chat/stream — same
    streaming path as a normal chat turn.
    """
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    client = get_user_client(current_user["token"])
    query = _build_query(body)

    # Title shape mirrors the chat router's auto-title path so the sidebar
    # entry reads consistently. Truncate the attendees blurb so the title
    # stays under the conversations.title_max constraint.
    attendees_snippet = body.attendees.strip()
    if len(attendees_snippet) > 30:
        attendees_snippet = attendees_snippet[:30].rstrip() + "…"
    title = f"Meeting prep: {body.meeting_type} — {attendees_snippet}"
    if len(title) > 80:
        title = title[:80]

    conv_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(
            lambda: client.table("conversations")
            .insert({
                "id": conv_id,
                "org_id": org_id,
                "user_id": user_id,
                "title": title,
                "created_at": now_iso,
                "updated_at": now_iso,
            })
            .execute()
        )
    except Exception as exc:
        log.exception("meeting_prep_conversation_create_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the meeting prep conversation.",
        ) from exc

    return {"conversation_id": conv_id, "prompt": query, "title": title}
