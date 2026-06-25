"""Calendar Intelligence service (#51, Agent2 Day 6).

Two flows:

  sync_upcoming_meetings(org_id, user_id)
    Pull events from Google Calendar for the next 48h, upsert into
    `calendar_meetings`. Brief generation is queued separately for each
    meeting that doesn't already have one.

  generate_meeting_prep_brief(meeting_id)
    Build a per-meeting prep brief: search KB for the topic + each
    attendee's name/company, synthesize structured brief. Save back to
    the meeting row.

The Inngest cron in `inngest/calendar_brief_functions.py` runs these on a
schedule; the router exposes the same operations for ad-hoc UI triggers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    search_facets_concurrent,
    synthesize_json,
)
from app.services.integrations import google_calendar

log = logging.getLogger(__name__)


# ── Sync ─────────────────────────────────────────────────────────────────────


async def sync_upcoming_meetings(
    *,
    org_id: str,
    user_id: str,
    hours_ahead: int = 48,
) -> list[dict[str, Any]]:
    """Fetch upcoming events and upsert. Returns the synced rows.

    On a PermissionError (not connected / scope missing) we return an empty
    list and log — the cron should skip silently rather than error every
    minute for users who haven't connected calendar.
    """
    try:
        events = await google_calendar.list_upcoming_events(
            org_id=org_id, user_id=user_id, hours_ahead=hours_ahead
        )
    except PermissionError:
        log.debug("calendar.sync.skip user=%s reason=not_connected", user_id)
        return []
    except Exception as exc:
        log.warning("calendar.sync.failed user=%s err=%s", user_id, exc)
        return []

    if not events:
        return []

    svc = get_service_client()
    now_iso = datetime.now(UTC).isoformat()
    upserted: list[dict[str, Any]] = []

    for ev in events:
        # Compute prep_brief_available_at = start - 2 hours.
        start_dt = _parse_iso(ev["start"])
        brief_available_at = (
            (start_dt - timedelta(hours=2)).isoformat() if start_dt else None
        )
        row = {
            "org_id": org_id,
            "user_id": user_id,
            "external_event_id": ev["id"],
            "calendar_id": "primary",
            "title": ev.get("title"),
            "description": ev.get("description") or "",
            "location": ev.get("location"),
            "attendee_emails": ev.get("attendees") or [],
            "start_time": ev["start"],
            "end_time": ev["end"],
            "meeting_url": ev.get("meeting_url"),
            "prep_brief_available_at": brief_available_at,
            "last_synced_at": now_iso,
        }
        # Upsert by (user_id, external_event_id).
        try:
            res = await asyncio.to_thread(
                lambda r=row: svc.table("calendar_meetings")
                .upsert(r, on_conflict="user_id,external_event_id")
                .execute()
            )
            if res.data:
                upserted.append(res.data[0])
        except Exception as exc:
            log.warning(
                "calendar.upsert.failed user=%s event=%s err=%s",
                user_id,
                ev["id"],
                exc,
            )

    return upserted


def _parse_iso(value: str) -> datetime | None:
    """Tolerant ISO/date parser — Google returns either 'YYYY-MM-DDTHH:MM:SS±HH:MM'
    for dateTime or 'YYYY-MM-DD' for all-day events. All-day → midnight UTC."""
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.fromisoformat(f"{value}T00:00:00+00:00")
    except ValueError:
        return None


# ── Prep brief generation ───────────────────────────────────────────────────


_BRIEF_SYSTEM = """You are a chief of staff prepping the user for a meeting in <2 hours. Produce a tight, actionable brief grounded ONLY in the company's internal context provided.

Sections to produce (each ≤80 words):
- executive_summary: the meeting in one paragraph, decisions needed, what good looks like
- attendee_context: per-attendee one-liner if context exists (who, why they care, recent interaction)
- topic_research: the meat — key facts, numbers, prior decisions on the meeting topic
- suggested_questions: 4–6 questions the user should ask (one bullet per question)

If a section has no signal, return an empty string rather than inventing.

Output JSON only:
{
  "executive_summary": "string",
  "attendee_context": "string",
  "topic_research": "string",
  "suggested_questions": ["string", ...]
}
""".strip()


async def generate_meeting_prep_brief(
    *,
    meeting_id: str,
) -> dict[str, Any]:
    """Search KB → synthesize → save to the row. Returns updated row.

    No-ops with a logged warning if the meeting has already been briefed
    (idempotent retry from Inngest cron is safe).
    """
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("calendar_meetings")
            .select("*")
            .eq("id", meeting_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    meeting = await asyncio.to_thread(_fetch)
    if not meeting:
        raise LookupError("meeting_not_found")
    if meeting.get("prep_brief_status") == "ready":
        return meeting

    await asyncio.to_thread(
        lambda: svc.table("calendar_meetings")
        .update({"prep_brief_status": "generating", "prep_brief_error": None})
        .eq("id", meeting_id)
        .execute()
    )

    try:
        org_id = meeting["org_id"]
        topic = meeting.get("title") or ""
        description = (meeting.get("description") or "").strip()
        attendees = meeting.get("attendee_emails") or []

        # Build the facet map: one for the topic, one for each unique attendee
        # company-domain (max 3 attendees to keep latency under 6s).
        facets: dict[str, str] = {
            "topic": f"meeting topic: {topic}. {description[:600]}".strip(),
        }
        company_set: list[str] = []
        for email in attendees[:5]:
            domain = (email.split("@", 1)[1] if "@" in email else "").lower()
            if not domain or domain in {"gmail.com", "outlook.com", "hotmail.com", "yahoo.com"}:
                continue
            company = domain.rsplit(".", 1)[0].split(".")[-1]
            if company not in company_set:
                company_set.append(company)
            if len(company_set) >= 3:
                break
        for i, c in enumerate(company_set):
            facets[f"attendee_{i+1}"] = f"interactions notes documents involving {c}"

        facet_results = await search_facets_concurrent(
            org_id=org_id, facets=facets, k=5, char_budget_per_facet=2000
        )
        context = build_context_block(facet_results)
        sources = collect_sources(facet_results)

        user_prompt = (
            f"## Meeting\n{topic}\n\n"
            + (f"## Description\n{description}\n\n" if description else "")
            + f"## Attendees\n{', '.join(attendees) if attendees else '(none on the invite)'}\n\n"
            + (f"## Company-internal context\n{context}\n\n" if context else "")
            + "## Output\nJSON only."
        )

        result = await synthesize_json(
            system_prompt=_BRIEF_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.3,
            timeout=60.0,
        )
        if not isinstance(result, dict):
            raise RuntimeError("brief_synthesis_returned_non_object")

        prep_brief = {
            "executive_summary": result.get("executive_summary") or "",
            "attendee_context": result.get("attendee_context") or "",
            "topic_research": result.get("topic_research") or "",
            "suggested_questions": [
                q for q in result.get("suggested_questions") or [] if isinstance(q, str)
            ],
            "source_documents": sources,
            "generated_at": datetime.now(UTC).isoformat(),
        }

        def _finalize() -> dict[str, Any]:
            res = (
                svc.table("calendar_meetings")
                .update(
                    {
                        "prep_brief": prep_brief,
                        "prep_brief_status": "ready",
                        "prep_brief_error": None,
                    }
                )
                .eq("id", meeting_id)
                .execute()
            )
            return (res.data or [{}])[0]

        return await asyncio.to_thread(_finalize)

    except Exception as exc:
        log.exception("calendar.brief.failed meeting=%s", meeting_id)
        await asyncio.to_thread(
            lambda: svc.table("calendar_meetings")
            .update({"prep_brief_status": "failed", "prep_brief_error": str(exc)})
            .eq("id", meeting_id)
            .execute()
        )
        raise


# ── Cron helper: which meetings need briefs right now ───────────────────────


async def meetings_needing_brief(*, lookahead_hours: int = 3) -> list[dict[str, Any]]:
    """Find calendar_meetings where prep_brief_status='pending' and
    prep_brief_available_at <= now() AND start_time > now() (so we don't
    waste compute on past meetings)."""
    svc = get_service_client()
    now_iso = datetime.now(UTC).isoformat()
    horizon = (datetime.now(UTC) + timedelta(hours=lookahead_hours)).isoformat()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("calendar_meetings")
            .select("id, org_id, user_id, start_time, prep_brief_available_at")
            .eq("prep_brief_status", "pending")
            .lte("prep_brief_available_at", now_iso)
            .gte("start_time", now_iso)
            .lte("start_time", horizon)
            .limit(200)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_fetch)


# ── User enumeration for the multi-user cron sweep ──────────────────────────


async def users_with_calendar_connection() -> list[tuple[str, str]]:
    """Returns [(org_id, user_id), …] for every user with a google_workspace
    integration that has calendar.readonly. Drives the per-user sync loop."""
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        res = (
            svc.table("integrations")
            .select("org_id, scope_user_id, scopes")
            .eq("provider", "google_workspace")
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)
    pairs: list[tuple[str, str]] = []
    for row in rows:
        scopes = row.get("scopes") or []
        if "https://www.googleapis.com/auth/calendar.readonly" not in scopes:
            continue
        if row.get("scope_user_id"):
            pairs.append((row["org_id"], row["scope_user_id"]))
    return pairs
