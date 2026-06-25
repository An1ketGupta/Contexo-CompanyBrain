"""Google Calendar API adapter (raw httpx).

Surface:
  list_upcoming_events(org_id, user_id, hours_ahead) -> list[event]
  create_event(org_id, user_id, ...) -> {event_id, url}

The Calendar Intelligence feature only needs to *read* events (calendar.readonly).
The Executive Assistant's optional follow-up scheduling needs to write, which
needs the broader `calendar.events` scope — we ship with read-only for v1 and
return PermissionError on write so the UI can prompt for re-consent rather
than silently failing.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.services.integrations.google_workspace import get_user_token

log = logging.getLogger(__name__)

_CAL_API = "https://www.googleapis.com/calendar/v3"
_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"


async def list_upcoming_events(
    *,
    org_id: str,
    user_id: str,
    hours_ahead: int = 48,
    calendar_id: str = "primary",
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Returns events between now and now+hours_ahead.

    We expand singleEvents=true and orderBy='startTime' so recurring meetings
    surface as their individual instances. Cancelled events are filtered
    client-side because Google's `showDeleted` defaults aren't quite what
    we want (it includes both deleted and cancelled-instance markers).
    """
    token = await get_user_token(org_id=org_id, user_id=user_id)
    if not token:
        raise PermissionError("google_workspace_not_connected")

    now = datetime.now(UTC)
    end = now + timedelta(hours=hours_ahead)

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_CAL_API}/calendars/{calendar_id}/events",
            params={
                "timeMin": now.isoformat().replace("+00:00", "Z"),
                "timeMax": end.isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": str(max_results),
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    if resp.status_code == 401:
        raise PermissionError("google_calendar_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("google_calendar_forbidden")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"google_calendar_list_failed: {resp.status_code} {resp.text[:200]}"
        )

    items = (resp.json() or {}).get("items") or []
    out: list[dict[str, Any]] = []
    for ev in items:
        if ev.get("status") == "cancelled":
            continue
        start = ((ev.get("start") or {}).get("dateTime")) or ((ev.get("start") or {}).get("date"))
        end_v = ((ev.get("end") or {}).get("dateTime")) or ((ev.get("end") or {}).get("date"))
        if not start or not end_v:
            continue
        attendees = [
            a.get("email")
            for a in (ev.get("attendees") or [])
            if a.get("email") and not a.get("self")
        ]
        out.append(
            {
                "id": ev["id"],
                "title": ev.get("summary") or "(no title)",
                "description": ev.get("description") or "",
                "location": ev.get("location") or "",
                "start": start,
                "end": end_v,
                "attendees": attendees,
                "meeting_url": _extract_meeting_url(ev),
                "html_link": ev.get("htmlLink"),
            }
        )
    return out


def _extract_meeting_url(event: dict[str, Any]) -> str | None:
    """Look for a Google Meet/Zoom/Teams URL in the event.

    Order: hangoutLink (Google Meet), then conferenceData.entryPoints (Zoom/
    Teams attached via add-on), then a regex scan of the description.
    """
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    cd = event.get("conferenceData") or {}
    for ep in cd.get("entryPoints") or []:
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]
    desc = event.get("description") or ""
    import re

    m = re.search(r"https?://[^\s\"<>]+(?:zoom\.us|teams\.microsoft\.com|meet\.google\.com)[^\s\"<>]*", desc)
    return m.group(0) if m else None


async def create_event(
    *,
    org_id: str,
    user_id: str,
    title: str,
    description: str,
    start: datetime,
    end: datetime,
    attendee_emails: list[str],
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """POST /calendars/{calendarId}/events. Requires the broader
    calendar.events scope; raises PermissionError if missing so the UI can
    drive a re-consent."""
    token = await get_user_token(org_id=org_id, user_id=user_id)
    if not token:
        raise PermissionError("google_workspace_not_connected")

    body = {
        "summary": title[:255],
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "attendees": [{"email": e} for e in attendee_emails if e],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            f"{_CAL_API}/calendars/{calendar_id}/events",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            params={"sendUpdates": "all"},
        )

    if resp.status_code == 401:
        raise PermissionError("google_calendar_unauthorized")
    if resp.status_code == 403:
        # 403 with insufficientPermissions almost always means the user
        # consented to calendar.readonly but not calendar.events. Distinct
        # error code so the UI prompts re-auth instead of retrying.
        raise PermissionError("google_calendar_scope_missing")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"google_calendar_create_failed: {resp.status_code} {resp.text[:200]}"
        )

    ev = resp.json() or {}
    return {"event_id": ev.get("id"), "url": ev.get("htmlLink")}
