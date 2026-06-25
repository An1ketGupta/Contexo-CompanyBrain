"""Proactive Morning Briefings (Feature 2.2).

Composes a weekly briefing for one user. Pulls signals from:
  * knowledge_gaps        — top 3 under-served queries in the past week
  * documents             — docs with review_due_at < now and health=stale
  * calendar_meetings     — meetings starting in the next 7 days
  * analytics_events      — high-activity event types from the past week

Synthesizes those into a 4-section markdown briefing via the org's LLM,
writes the resulting row to `briefings`, and dispatches an email +
in-app notification per the user's preferences.

Idempotency: every briefing is uniquely keyed by (user_id, period_key)
where period_key is the ISO week. A re-fire of the cron is a no-op for any
user who already received this week's briefing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database import get_service_client
from app.services.llm import Message, get_llm_client
from app.services.notifications import create_notification

log = logging.getLogger(__name__)


_BRIEFING_PROMPT = """You are the user's chief of staff. Write a short Monday-morning briefing that helps them walk into the week informed. The briefing must be MARKDOWN with these four sections in order:

## What's coming up this week
(1-2 short sentences naming the most important upcoming meetings.)

## What needs your attention
(1-2 short sentences calling out stale docs or knowledge gaps the user can fix.)

## What the team is asking about
(1-2 sentences summarizing the topics generating questions, citing the most-asked queries.)

## Suggested next step
(One concrete action — open a doc, draft a note, attend a meeting.)

Rules:
- Lead with the user's first name if provided.
- Total length 120-220 words.
- Be specific: name documents, meeting titles, topics from the data.
- If a section has no data, say so honestly in one sentence (e.g. "No
  meetings on the calendar this week — good time to revisit X.").
- No emoji, no bullet lists (prose only inside sections), no preamble or sign-off."""


# ── Public entrypoint ──────────────────────────────────────────────────────


async def generate_briefing_for_user(
    *, org_id: str, user_id: str, period_key: str
) -> dict[str, Any] | None:
    """Build, persist, and deliver a briefing for one user.

    Returns the inserted row, or None if (user, period_key) already had one.
    Failures past the "row inserted" stage flip status=failed but don't
    raise — Inngest doesn't need to retry a successfully-recorded briefing.
    """
    svc = get_service_client()

    # Idempotency: pre-flight the UNIQUE constraint with a select. Cheaper
    # than catching the integrity error and easier to log clearly.
    existing = await asyncio.to_thread(
        lambda: svc.table("briefings")
        .select("id, status")
        .eq("user_id", user_id)
        .eq("period_key", period_key)
        .maybe_single()
        .execute()
    )
    if existing and existing.data:
        log.info("briefing_already_exists user=%s period=%s", user_id, period_key)
        return None

    # 1. Insert a placeholder so re-fires hit the dedupe path above.
    placeholder = {
        "org_id": org_id,
        "user_id": user_id,
        "status": "generating",
        "period_key": period_key,
    }
    try:
        ins = await asyncio.to_thread(
            lambda: svc.table("briefings").insert(placeholder).execute()
        )
        row = (ins.data or [None])[0]
    except Exception as exc:
        # Race with another worker — somebody else just inserted this key.
        log.info("briefing_race user=%s period=%s err=%s", user_id, period_key, exc)
        return None
    if not row:
        return None
    briefing_id: str = row["id"]

    try:
        # 2. Load the user profile + preferences (for name, channels).
        profile = await _load_user_profile(svc, user_id)
        if not profile:
            await _mark_failed(svc, briefing_id, "user not found")
            return row

        # 3. Gather signals.
        data = await _gather_signals(org_id=org_id, user_id=user_id)

        # 4. LLM synthesize prose.
        body_md, summary = await _synthesize(profile=profile, data=data)

        # 5. Persist final.
        delivered_at = datetime.now(UTC).isoformat()
        await asyncio.to_thread(
            lambda: svc.table("briefings")
            .update({
                "status": "ok",
                "body_md": body_md,
                "summary": summary,
                "data": data,
            })
            .eq("id", briefing_id)
            .execute()
        )

        # 6. Deliver.
        prefs = profile.get("prefs") or {}
        if prefs.get("via_inapp", True):
            await _send_inapp(
                org_id=org_id,
                user_id=user_id,
                briefing_id=briefing_id,
                period_key=period_key,
                summary=summary,
            )
            await asyncio.to_thread(
                lambda: svc.table("briefings")
                .update({"delivered_inapp_at": delivered_at})
                .eq("id", briefing_id)
                .execute()
            )
        if prefs.get("via_email", True):
            email = profile.get("email")
            if email:
                # Pull org name once for the email shell.
                org_row = await asyncio.to_thread(
                    lambda: svc.table("organizations")
                    .select("name")
                    .eq("id", org_id)
                    .maybe_single()
                    .execute()
                )
                org_name = ((org_row.data if org_row else None) or {}).get("name")
                ok = await _send_email(
                    org_id=org_id,
                    user_id=user_id,
                    to_email=email,
                    body_md=body_md,
                    summary=summary,
                    period_key=period_key,
                    recipient_name=profile.get("name"),
                    org_name=org_name,
                    briefing_id=briefing_id,
                )
                if ok:
                    await asyncio.to_thread(
                        lambda: svc.table("briefings")
                        .update({"delivered_email_at": delivered_at})
                        .eq("id", briefing_id)
                        .execute()
                    )

        # 7. Mark prefs.last_sent_at so the cron skips a same-week refire.
        await asyncio.to_thread(
            lambda: svc.table("briefing_preferences")
            .update({"last_sent_at": delivered_at})
            .eq("user_id", user_id)
            .execute()
        )

        return row
    except Exception as exc:
        log.exception("briefing_generate_failed user=%s err=%s", user_id, exc)
        await _mark_failed(svc, briefing_id, str(exc)[:500])
        return row


# ── Helpers ────────────────────────────────────────────────────────────────


async def _mark_failed(svc, briefing_id: str, message: str) -> None:
    await asyncio.to_thread(
        lambda: svc.table("briefings")
        .update({"status": "failed", "error_message": message})
        .eq("id", briefing_id)
        .execute()
    )


async def _load_user_profile(svc, user_id: str) -> dict[str, Any] | None:
    """Combine users + briefing_preferences in two cheap selects."""
    user_res = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id, display_name, email, persona")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    user = (user_res.data if user_res else None) or {}
    if not user:
        return None

    prefs_res = await asyncio.to_thread(
        lambda: svc.table("briefing_preferences")
        .select("topics, via_email, via_inapp, timezone")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    prefs = (prefs_res.data if prefs_res else None) or {}
    return {
        "id": user["id"],
        "name": user.get("display_name") or (user.get("email") or "").split("@")[0],
        "email": user.get("email"),
        "persona": user.get("persona"),
        "prefs": prefs,
    }


async def _gather_signals(*, org_id: str, user_id: str) -> dict[str, Any]:
    svc = get_service_client()
    now = datetime.now(UTC)
    week_ago = (now - timedelta(days=7)).isoformat()
    week_ahead = (now + timedelta(days=7)).isoformat()

    def _knowledge_gaps() -> list[dict[str, Any]]:
        # Tally by topic over the last 7 days. supabase-py doesn't expose
        # GROUP BY directly, so we pull recent gaps and tally in Python.
        res = (
            svc.table("knowledge_gaps")
            .select("topic, query, created_at")
            .eq("org_id", org_id)
            .gte("created_at", week_ago)
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        rows = res.data or []
        tally: dict[str, dict[str, Any]] = {}
        for r in rows:
            t = (r.get("topic") or "").strip().lower()
            if not t:
                continue
            slot = tally.setdefault(t, {"topic": t, "count": 0, "last_query": ""})
            slot["count"] += 1
            slot["last_query"] = r.get("query") or slot["last_query"]
        ranked = sorted(tally.values(), key=lambda r: r["count"], reverse=True)
        return ranked[:3]

    def _stale_docs() -> list[dict[str, Any]]:
        # Docs the user is on the hook for that need review.
        res = (
            svc.table("documents")
            .select("id, name, health_label, review_due_at, last_reviewed_at")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .eq("created_by", user_id)
            .lte("review_due_at", now.isoformat())
            .limit(5)
            .execute()
        )
        return res.data or []

    def _upcoming_meetings() -> list[dict[str, Any]]:
        res = (
            svc.table("calendar_meetings")
            .select("id, title, start_time, attendee_emails")
            .eq("org_id", org_id)
            .eq("user_id", user_id)
            .gte("start_time", now.isoformat())
            .lte("start_time", week_ahead)
            .order("start_time")
            .limit(5)
            .execute()
        )
        return res.data or []

    def _trending_topics() -> list[dict[str, Any]]:
        # Surface event_type counts as a coarse "what's the team doing?" signal.
        res = (
            svc.table("analytics_events")
            .select("event_type, created_at")
            .eq("org_id", org_id)
            .gte("created_at", week_ago)
            .limit(2000)
            .execute()
        )
        rows = res.data or []
        tally: dict[str, int] = {}
        for r in rows:
            et = r.get("event_type") or "unknown"
            tally[et] = tally.get(et, 0) + 1
        ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
        return [{"event_type": k, "count": v} for k, v in ranked[:5]]

    gaps, stale, meetings, trending = await asyncio.gather(
        asyncio.to_thread(_knowledge_gaps),
        asyncio.to_thread(_stale_docs),
        asyncio.to_thread(_upcoming_meetings),
        asyncio.to_thread(_trending_topics),
    )
    return {
        "knowledge_gaps": gaps,
        "stale_docs": stale,
        "meetings": meetings,
        "trending_topics": trending,
    }


async def _synthesize(
    *, profile: dict[str, Any], data: dict[str, Any]
) -> tuple[str, str]:
    """Call the LLM. Returns (body_markdown, summary). Falls back to a
    deterministic template if the LLM fails — we never want to leave the
    user with no content at all on a Monday morning."""
    name = profile.get("name") or "there"
    lines: list[str] = [f"User name: {name}"]
    if profile.get("persona"):
        lines.append(f"User persona: {profile['persona']}")

    meetings = data.get("meetings") or []
    if meetings:
        lines.append("\nUpcoming meetings (next 7 days):")
        for m in meetings:
            t = m.get("title") or "Untitled meeting"
            when = (m.get("start_time") or "")[:16]
            lines.append(f"- {t} at {when}")
    else:
        lines.append("\nUpcoming meetings: none on the calendar.")

    gaps = data.get("knowledge_gaps") or []
    if gaps:
        lines.append("\nTop questions teammates asked the AI last week:")
        for g in gaps:
            lines.append(f"- “{g.get('topic')}” ({g.get('count')} times)")
    else:
        lines.append("\nKnowledge gaps last week: none flagged.")

    stale = data.get("stale_docs") or []
    if stale:
        lines.append("\nDocs you own that are due for review:")
        for d in stale:
            lines.append(f"- {d.get('name')}")
    else:
        lines.append("\nDocs due for review (owned by you): none.")

    trending = data.get("trending_topics") or []
    if trending:
        lines.append("\nHigh-activity event types last week:")
        for t in trending[:3]:
            lines.append(f"- {t['event_type']}: {t['count']}")

    user_prompt = "\n".join(lines)

    try:
        client = get_llm_client()
        res = await client.complete(
            messages=[
                Message(role="user", content=user_prompt),
            ],
            system_extra=_BRIEFING_PROMPT,
            temperature=0.4,
            timeout=30.0,
        )
        body = (getattr(res, "text", "") or "").strip()
        if body:
            # Summary = first non-empty line stripped of markdown emphasis.
            summary_line = next(
                (l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("#")),
                "Your weekly briefing is ready.",
            )
            summary = summary_line[:200]
            return body, summary
    except Exception as exc:
        log.warning("briefing_llm_failed err=%s", exc)

    # Fallback prose — never block delivery on LLM hiccup.
    parts: list[str] = []
    parts.append(f"Hi {name}, here's your weekly snapshot.")
    if meetings:
        parts.append(f"You have {len(meetings)} meeting(s) coming up this week.")
    else:
        parts.append("No meetings on the calendar this week.")
    if stale:
        parts.append(
            f"{len(stale)} document(s) you own are due for review."
        )
    if gaps:
        top = gaps[0].get("topic") if gaps else None
        if top:
            parts.append(f"The team's been asking about \"{top}\" — worth a look.")
    body = "## Your weekly briefing\n\n" + " ".join(parts)
    return body, parts[0]


async def _send_inapp(
    *,
    org_id: str,
    user_id: str,
    briefing_id: str,
    period_key: str,
    summary: str,
) -> None:
    await create_notification(
        org_id=org_id,
        user_id=user_id,
        type="weekly_briefing",
        title="Your weekly briefing is ready",
        body=summary,
        link_url=f"/briefings/{briefing_id}",
        metadata={"briefing_id": briefing_id, "period_key": period_key},
        dedupe_key=f"briefing:{period_key}",
    )


async def _send_email(
    *,
    org_id: str,
    user_id: str,
    to_email: str,
    body_md: str,
    summary: str,
    period_key: str,
    recipient_name: str | None,
    org_name: str | None,
    briefing_id: str,
) -> bool:
    """Hand off to the email worker. Returns True on enqueue success."""
    try:
        from app.config import settings
        from app.services.email.dispatcher import send_email_event

        app_url = (getattr(settings, "app_url", None)
                   or getattr(settings, "frontend_url", None)
                   or "https://app.nirnayaiq.com")
        await send_email_event(
            event_type="weekly_briefing",
            to=to_email,
            user_id=user_id,
            org_id=org_id,
            dedupe_key=f"briefing:{period_key}",
            data={
                "recipient_name": recipient_name,
                "org_name": org_name or "your workspace",
                "app_url": app_url,
                "briefing_url": f"{app_url}/briefings/{briefing_id}",
                "summary": summary,
                "body_markdown": body_md,
                "period_key": period_key,
            },
        )
        return True
    except Exception as exc:
        log.warning("briefing_email_failed user=%s err=%s", user_id, exc)
        return False


# ── Cron support: who's due this Monday morning? ───────────────────────────


def current_period_key(now: datetime | None = None) -> str:
    """Return an ISO week key like '2026-W26' for the given (or current) UTC time."""
    now = now or datetime.now(UTC)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


async def find_due_recipients(*, now: datetime | None = None) -> list[dict[str, str]]:
    """Return [{org_id, user_id}] of recipients whose local time matches their
    configured (weekday, hour) and who haven't received this week's briefing.

    Called by the hourly cron — fan-out is keyed by `period_key` so any
    delayed retry within the same week is a dedup.
    """
    svc = get_service_client()
    now = now or datetime.now(UTC)
    period_key = current_period_key(now)

    def _query() -> list[dict[str, Any]]:
        res = (
            svc.table("briefing_preferences")
            .select("user_id, org_id, weekday, hour, timezone")
            .eq("enabled", True)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_query)
    due: list[dict[str, str]] = []
    for r in rows:
        tzname = r.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tzname)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        local = now.astimezone(tz)
        # ISO-style weekday in Postgres EXTRACT(DOW) is 0=Sun..6=Sat. Match
        # Python's `.weekday()` (Mon=0..Sun=6) via translation.
        py_weekday = local.weekday()  # 0=Mon..6=Sun
        pg_weekday = (py_weekday + 1) % 7  # 0=Sun..6=Sat
        if pg_weekday != int(r.get("weekday") or 1):
            continue
        if local.hour != int(r.get("hour") or 8):
            continue
        due.append({
            "user_id": r["user_id"],
            "org_id": r["org_id"],
            "period_key": period_key,
        })
    return due
