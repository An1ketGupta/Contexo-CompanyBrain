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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.services.agents.kb_synthesis import (
    build_context_block,
    collect_sources,
    fetch_document_text,
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
        # Compute prep_brief_available_at = start - 5 hours.
        start_dt = _parse_iso(ev["start"])
        brief_available_at = (
            (start_dt - timedelta(hours=5)).isoformat() if start_dt else None
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
            "recurring_event_id": ev.get("recurring_event_id"),
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


# Cosine floor for the brief's KB retrieval. Below this, a chunk is not
# meaningfully about the meeting. Matches RFP gap detection (0.55): org-wide
# policy docs (Remote Work, Leave, Expense) are broadly relevant to *any*
# meeting and land in the ~0.4–0.55 band for a bare title, so a lower floor
# lets them masquerade as topical grounding. This catches meetings that have a
# real title but no KB match; `_has_topic_signal` catches the ones whose title
# is itself contentless (see below).
_BRIEF_MIN_SIMILARITY = 0.55

# Words that carry no subject matter — a meeting whose title is nothing but
# these (plus short abbreviations) has no topic to brief. "TG Meet", "Weekly
# Sync", "Daily Standup", "1:1" all collapse to zero meaningful tokens.
_MEETING_FILLER = frozenset(
    {
        "meet", "meeting", "sync", "call", "standup", "stand", "up", "catchup",
        "catch", "chat", "huddle", "session", "checkin", "check", "in", "touchbase",
        "touch", "base", "weekly", "daily", "monthly", "quarterly", "biweekly",
        "annual", "oneonone", "the", "and", "with", "for", "team", "quick", "brief",
    }
)


def _has_topic_signal(title: str, description: str) -> bool:
    """True if the meeting carries any subject matter to ground a brief on.

    A description of real length is signal on its own. Otherwise we need the
    title to contain at least one meaningful token: alphabetic, ≥3 chars, and
    not a generic meeting-filler word. "TG Meet" → 'tg' (too short) + 'meet'
    (filler) → no signal → skip rather than hallucinate a brief from policy docs.
    """
    import re

    if len((description or "").strip()) >= 15:
        return True
    tokens = re.findall(r"[a-zA-Z]+", title or "")
    meaningful = [t for t in tokens if len(t) >= 3 and t.lower() not in _MEETING_FILLER]
    return len(meaningful) >= 1


_BRIEF_SYSTEM = """You are a chief of staff prepping the user for a meeting in <5 hours. Produce a tight, actionable brief grounded ONLY in the context provided.

When a "## Previous meeting" section is present, it is the PRIMARY grounding: this is a recurring/repeated meeting, so write a continuity brief. Lean on what the last session decided and left open; treat "## Company-internal context (secondary)" as supporting detail only.

Sections to produce (each ≤80 words):
- executive_summary: where things stand coming in — what the last session settled, what's still open, and what a good outcome looks like this time (if no prior meeting, the meeting in one paragraph + decisions needed)
- attendee_context: per-attendee one-liner grounded in real prior contributions/ownership if present (who, what they own, their open items)
- topic_research: carried-over decisions, open threads, and key facts/numbers on the meeting topic
- suggested_questions: 4–6 concrete follow-ups to drive the meeting ("Did X ship?", "Status on Y?")

If a section has no signal, return an empty string rather than inventing. Never invent owners, dates, or decisions.

Output JSON only:
{
  "executive_summary": "string",
  "attendee_context": "string",
  "topic_research": "string",
  "suggested_questions": ["string", ...]
}
""".strip()


# ── Prior-meeting grounding ─────────────────────────────────────────────────
#
# The strongest grounding for a recurring/repeated meeting is its OWN previous
# occurrence — what was decided, what's still open. We resolve that here and
# feed it to the brief as primary context, ahead of the generic company KB.

_TRANSCRIPT_SOURCES = {"google_meet_transcript", "zoom", "teams_transcript"}

_RECAP_SYSTEM = """You are condensing a raw meeting transcript into a compact recap for prepping the NEXT occurrence of this meeting. Ground strictly in the transcript.

Output JSON only:
{
  "recap": "2-4 sentence summary of what this meeting covered",
  "decisions": ["decision made", ...],
  "action_items": ["owner: task (due)", ...],
  "open_threads": ["unresolved item to follow up on next time", ...]
}
Use empty arrays/string for anything the transcript doesn't support. Do not invent owners, dates, or decisions.
""".strip()


@dataclass
class PriorContext:
    """Resolved grounding from a meeting's previous occurrence."""

    recap_text: str
    source_doc: dict[str, Any] | None
    meeting_date: str | None
    prior_title: str | None


def _normalize_title(title: str) -> str:
    """Lower/trim/collapse-whitespace/strip edge punctuation for title matching.

    Intentionally does NOT strip filler words (unlike `_has_topic_signal`): two
    occurrences of the same meeting must compare equal, so "TG Meet" has to
    match "TG Meet" verbatim after normalization.
    """
    import re

    t = re.sub(r"\s+", " ", (title or "").lower()).strip()
    return t.strip(" -–—:|.")


async def find_previous_occurrence(meeting: dict[str, Any]) -> dict[str, Any] | None:
    """The most recent earlier meeting that is the same series as `meeting`.

    Primary key: Google `recurring_event_id` (exact). Fallback: same owner +
    identical normalized title + earlier start (catches meetings recreated
    ad-hoc rather than as a true recurring series).
    """
    svc = get_service_client()
    user_id = meeting.get("user_id")
    start_time = meeting.get("start_time")
    if not user_id or not start_time:
        return None

    rec_id = meeting.get("recurring_event_id")
    if rec_id:

        def _by_series() -> list[dict[str, Any]]:
            res = (
                svc.table("calendar_meetings")
                .select("*")
                .eq("user_id", user_id)
                .eq("recurring_event_id", rec_id)
                .lt("start_time", start_time)
                .order("start_time", desc=True)
                .limit(1)
                .execute()
            )
            return res.data or []

        rows = await asyncio.to_thread(_by_series)
        if rows:
            return rows[0]

    norm = _normalize_title(meeting.get("title") or "")
    if not norm:
        return None

    def _recent_past() -> list[dict[str, Any]]:
        res = (
            svc.table("calendar_meetings")
            .select("*")
            .eq("user_id", user_id)
            .lt("start_time", start_time)
            .order("start_time", desc=True)
            .limit(25)
            .execute()
        )
        return res.data or []

    for c in await asyncio.to_thread(_recent_past):
        if _normalize_title(c.get("title") or "") == norm:
            return c
    return None


def _format_structured_recap(summary: dict[str, Any]) -> str:
    """Render a Zoom/Teams `meeting_summaries` row into recap text."""
    parts: list[str] = []
    if (summary.get("summary") or "").strip():
        parts.append(summary["summary"].strip())
    decisions = [
        (d.get("text") if isinstance(d, dict) else str(d))
        for d in (summary.get("decisions") or [])
    ]
    decisions = [d for d in decisions if d]
    if decisions:
        parts.append("Decisions:\n" + "\n".join(f"- {d}" for d in decisions))
    items: list[str] = []
    for a in summary.get("action_items") or []:
        if isinstance(a, dict):
            owner = a.get("owner") or "Unassigned"
            task = a.get("task") or ""
            due = a.get("due")
            line = f"{owner}: {task}" + (f" (due {due})" if due and due != "unspecified" else "")
            items.append(line.strip())
        elif a:
            items.append(str(a))
    if items:
        parts.append("Action items:\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(parts)


def _format_recap_dict(recap: dict[str, Any]) -> str:
    """Render a generated (Google Meet) recap dict into recap text."""
    parts: list[str] = []
    if (recap.get("recap") or "").strip():
        parts.append(recap["recap"].strip())
    for label, key in (("Decisions", "decisions"), ("Action items", "action_items"), ("Open threads", "open_threads")):
        vals = [str(v).strip() for v in (recap.get(key) or []) if str(v).strip()]
        if vals:
            parts.append(f"{label}:\n" + "\n".join(f"- {v}" for v in vals))
    return "\n\n".join(parts)


async def _summarize_prior_transcript(text: str) -> dict[str, Any]:
    result = await synthesize_json(
        system_prompt=_RECAP_SYSTEM,
        user_prompt=f"## Transcript\n{text}\n\n## Output\nJSON only.",
        temperature=0.2,
        timeout=60.0,
    )
    return result if isinstance(result, dict) else {}


async def _fetch_meeting_summary(
    svc: Any, org_id: str, document_id: str
) -> dict[str, Any] | None:
    def _q() -> dict[str, Any] | None:
        res = (
            svc.table("meeting_summaries")
            .select("summary, decisions, action_items")
            .eq("org_id", org_id)
            .eq("source_document_id", document_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    return await asyncio.to_thread(_q)


async def _get_or_build_recap(svc: Any, org_id: str, document_id: str) -> str | None:
    """Recap of a Google Meet transcript doc, cached on documents.metadata."""
    def _meta() -> dict[str, Any]:
        res = (
            svc.table("documents")
            .select("metadata")
            .eq("id", document_id)
            .maybe_single()
            .execute()
        )
        return ((res.data or {}).get("metadata") if res else None) or {}

    metadata = await asyncio.to_thread(_meta)
    cached = metadata.get("prior_meeting_recap")
    if isinstance(cached, dict) and cached.get("recap"):
        return _format_recap_dict(cached)

    text = await fetch_document_text(document_id=document_id, org_id=org_id, char_budget=16000)
    if not text.strip():
        return None
    recap = await _summarize_prior_transcript(text)
    if not recap.get("recap"):
        return None

    metadata["prior_meeting_recap"] = {**recap, "generated_at": datetime.now(UTC).isoformat()}
    await asyncio.to_thread(
        lambda: svc.table("documents").update({"metadata": metadata}).eq("id", document_id).execute()
    )
    return _format_recap_dict(recap)


async def resolve_prior_meeting_context(
    prior: dict[str, Any],
    *,
    series_match: bool = False,
) -> PriorContext | None:
    """Turn a previous-occurrence meeting into a compact grounding block.

    Resolves the meeting's transcript document (owned by the meeting owner,
    ingested within a window around the meeting), then either reuses its
    structured Zoom/Teams `meeting_summaries` or recaps the raw Google Meet
    transcript.

    Confidence gate for a raw transcript: its name must match the meeting title
    OR `series_match` must be True. `series_match` means the caller found this
    prior occurrence via the Google recurring-series key (`recurring_event_id`),
    not a title guess — a link strong enough to survive a mid-series rename,
    where the transcript's frozen name no longer equals the renamed title. A
    structured summary is always trusted. Returns None when nothing links
    confidently (never guesses).
    """
    svc = get_service_client()
    org_id = prior.get("org_id")
    user_id = prior.get("user_id")
    start = _parse_iso(prior.get("start_time") or "")
    if not org_id or not user_id or not start:
        return None

    lo = (start - timedelta(hours=1)).isoformat()
    hi = (start + timedelta(hours=6)).isoformat()

    def _docs() -> list[dict[str, Any]]:
        res = (
            svc.table("documents")
            .select("id, name, source, created_by, created_at")
            .eq("org_id", org_id)
            .eq("created_by", user_id)
            .gte("created_at", lo)
            .lte("created_at", hi)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []

    cands = [d for d in await asyncio.to_thread(_docs) if d.get("source") in _TRANSCRIPT_SOURCES]
    if not cands:
        return None

    title_norm = _normalize_title(prior.get("title") or "")
    title_matched = [
        d for d in cands if title_norm and title_norm in _normalize_title(d.get("name") or "")
    ]
    # Title matches first (strongest link). The rest follow ordered by how close
    # each was filed to the meeting's start time — so when we fall back to the
    # series key (rename case), the transcript nearest the call wins over other
    # transcripts that happen to sit in the same window.
    rest = [d for d in cands if d not in title_matched]
    rest.sort(
        key=lambda d: abs(
            ((_parse_iso(d.get("created_at") or "") or start) - start).total_seconds()
        )
    )
    ordered = title_matched + rest

    for d in ordered:
        doc_id = d["id"]
        source_doc = {"document_id": doc_id, "document_name": d.get("name")}
        summary = await _fetch_meeting_summary(svc, org_id, doc_id)
        if summary:
            recap = _format_structured_recap(summary)
            if recap.strip():
                return PriorContext(recap, source_doc, prior.get("start_time"), prior.get("title"))
        # Raw Google Meet transcript: trust it when its name matches the title,
        # or when the series key already proved same-series (rename-safe).
        if d.get("source") == "google_meet_transcript" and (d in title_matched or series_match):
            recap = await _get_or_build_recap(svc, org_id, doc_id)
            if recap and recap.strip():
                if d not in title_matched:
                    log.info(
                        "calendar.brief.prior_context.series_rename_match doc=%s", doc_id
                    )
                return PriorContext(recap, source_doc, prior.get("start_time"), prior.get("title"))
    return None


async def generate_meeting_prep_brief(
    *,
    meeting_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Search KB → synthesize → save to the row. Returns updated row.

    Returns the existing row untouched if the meeting has already been briefed
    (idempotent retry from the Inngest cron is safe). Pass `force=True` for the
    on-demand "Regenerate brief" path, which must re-run even when a brief is
    already 'ready'.
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
    if not force and meeting.get("prep_brief_status") == "ready":
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

        # Derive company-domain attendees (max 3, personal domains excluded) —
        # both a retrieval facet and a signal that the meeting has *someone*
        # worth researching even if the title is generic.
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

        # Resolve the previous occurrence's transcript/summary. This is the
        # primary grounding for recurring meetings and a third "signal" for the
        # skip guard below: a generic-titled "TG Meet" is perfectly briefable if
        # we have last week's transcript to continue from.
        prior = await find_previous_occurrence(meeting)
        # A shared, non-null recurring_event_id means `prior` was located by the
        # exact Google series key — trustworthy even if the meeting was renamed
        # mid-series, so the transcript resolver can accept a name that no longer
        # matches the (renamed) title.
        series_match = bool(
            prior
            and meeting.get("recurring_event_id")
            and prior.get("recurring_event_id") == meeting.get("recurring_event_id")
        )
        prior_ctx = (
            await resolve_prior_meeting_context(prior, series_match=series_match)
            if prior
            else None
        )
        if prior_ctx:
            log.info(
                "calendar.brief.prior_context meeting=%s doc=%s",
                meeting_id,
                (prior_ctx.source_doc or {}).get("document_id"),
            )

        # No prior meeting to continue from, no topical substance in the
        # title/description, AND no company attendee to research → there is
        # nothing meeting-specific to ground a brief on. Skip up front rather
        # than let retrieval match org-wide policy docs and the LLM spin them
        # into a plausible-but-invented brief. (The "TG Meet with two gmail
        # addresses and no agenda" case.)
        if prior_ctx is None and not _has_topic_signal(topic, description) and not company_set:
            log.info(
                "calendar.brief.skipped meeting=%s reason=no_topic_signal", meeting_id
            )

            def _skip_low_signal() -> dict[str, Any]:
                res = (
                    svc.table("calendar_meetings")
                    .update(
                        {
                            "prep_brief": None,
                            "prep_brief_status": "skipped",
                            "prep_brief_error": None,
                        }
                    )
                    .eq("id", meeting_id)
                    .execute()
                )
                return (res.data or [{}])[0]

            return await asyncio.to_thread(_skip_low_signal)

        # Build the facet map: one for the topic, one per company-domain attendee.
        facets: dict[str, str] = {
            "topic": f"meeting topic: {topic}. {description[:600]}".strip(),
        }
        for i, c in enumerate(company_set):
            facets[f"attendee_{i+1}"] = f"interactions notes documents involving {c}"

        facet_results = await search_facets_concurrent(
            org_id=org_id,
            facets=facets,
            k=5,
            char_budget_per_facet=2000,
            min_similarity=_BRIEF_MIN_SIMILARITY,
        )
        context = build_context_block(facet_results)
        sources = collect_sources(facet_results)

        # No prior-meeting grounding AND no chunk cleared the relevance floor —
        # the meeting topic/attendees have no grounding anywhere. Skip synthesis
        # rather than hand the LLM an empty context and hope it returns empty
        # sections; in practice it fabricates a plausible brief from thin air
        # (the exact failure this guard exists to prevent). Clear any stale
        # brief too. (When prior_ctx exists we always proceed — it's primary.)
        if not context and prior_ctx is None:
            log.info(
                "calendar.brief.skipped meeting=%s reason=no_relevant_context",
                meeting_id,
            )

            def _skip() -> dict[str, Any]:
                res = (
                    svc.table("calendar_meetings")
                    .update(
                        {
                            "prep_brief": None,
                            "prep_brief_status": "skipped",
                            "prep_brief_error": None,
                        }
                    )
                    .eq("id", meeting_id)
                    .execute()
                )
                return (res.data or [{}])[0]

            return await asyncio.to_thread(_skip)

        prior_block = ""
        if prior_ctx:
            when = ""
            dt = _parse_iso(prior_ctx.meeting_date or "")
            if dt:
                when = f" ({dt.date().isoformat()})"
            prior_block = (
                f"## Previous meeting{when}\n"
                "This is the PRIMARY grounding — prefer it. Continue from where the last "
                "session left off; surface open action items and follow-ups.\n"
                f"{prior_ctx.recap_text}\n\n"
            )

        user_prompt = (
            f"## Meeting\n{topic}\n\n"
            + (f"## Description\n{description}\n\n" if description else "")
            + f"## Attendees\n{', '.join(attendees) if attendees else '(none on the invite)'}\n\n"
            + prior_block
            + (f"## Company-internal context (secondary)\n{context}\n\n" if context else "")
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

        # Surface the prior transcript in "Based on" (front, it's the primary
        # source) and expose a provenance stub for the UI's continuity chip.
        if prior_ctx and prior_ctx.source_doc:
            sources = [
                {**prior_ctx.source_doc, "similarity": 1.0, "section_heading": None},
                *[s for s in sources if s.get("document_id") != prior_ctx.source_doc.get("document_id")],
            ]

        prep_brief = {
            "executive_summary": result.get("executive_summary") or "",
            "attendee_context": result.get("attendee_context") or "",
            "topic_research": result.get("topic_research") or "",
            "suggested_questions": [
                q for q in result.get("suggested_questions") or [] if isinstance(q, str)
            ],
            "source_documents": sources,
            "prior_meeting": (
                {"title": prior_ctx.prior_title, "date": prior_ctx.meeting_date}
                if prior_ctx
                else None
            ),
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
    waste compute on meeting notes)."""
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
