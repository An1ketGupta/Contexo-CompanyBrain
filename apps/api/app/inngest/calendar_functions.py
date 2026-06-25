"""Inngest worker for Calendar Intelligence (#51, Agent2 Day 6).

Three functions:

  calendar-sync-cron        (cron */15 minutes)
      Fans out per-user "calendar/sync.user" events. We don't sync all users
      inside one function so a slow user (eg 500-event calendar) doesn't
      block the others.

  calendar-sync-user        (event: calendar/sync.user)
      For one (org, user), call sync_upcoming_meetings. Best-effort — a
      Google API failure marks the integration's last_error but does not
      retry forever.

  calendar-generate-briefs  (cron */15 minutes, offset)
      Find meetings whose prep_brief_available_at is in the past and
      prep_brief_status='pending', fan out per-meeting brief jobs.

  calendar-brief-meeting    (event: calendar/brief.meeting)
      Call generate_meeting_prep_brief for one meeting.

Cadence picked at every 15 minutes for both crons — Calendar Intelligence is
not minute-sensitive (the user wants the brief ~2h before the meeting, not
exactly), and 15-min granularity keeps the worker churn modest for a side
feature.
"""
from __future__ import annotations

import logging
from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.services.calendar_intelligence import (
    generate_meeting_prep_brief,
    meetings_needing_brief,
    sync_upcoming_meetings,
    users_with_calendar_connection,
)

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="calendar-sync-cron",
    trigger=inngest.TriggerCron(cron="*/15 * * * *"),
    retries=0,
)
async def calendar_sync_cron(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    pairs = await step.run("list-connections", users_with_calendar_connection)
    if not pairs:
        return {"users": 0}
    for org_id, user_id in pairs:
        # Each user gets its own event so failures stay isolated. Idempotency
        # key bounds re-enqueue churn if this cron runs twice in the same
        # 15-min window for any reason (worker restart, manual replay).
        await step.send_event(
            f"emit-{user_id}",
            inngest.Event(
                name="calendar/sync.user",
                data={"org_id": org_id, "user_id": user_id},
                id=f"calsync-{user_id}-{ctx.run_id[:8]}",
            ),
        )
    return {"users": len(pairs)}


@_inngest_client.create_function(
    fn_id="calendar-sync-user",
    trigger=inngest.TriggerEvent(event="calendar/sync.user"),
    concurrency=[
        # One sync per user at a time. Google's per-user rate limit gives us
        # plenty of headroom but in-flight overlap can race on the upsert.
        inngest.Concurrency(limit=1, key="event.data.user_id", scope="fn"),
    ],
    retries=1,
)
async def calendar_sync_user(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    org_id = data["org_id"]
    user_id = data["user_id"]
    rows = await step.run(
        f"sync-{user_id}",
        lambda: sync_upcoming_meetings(org_id=org_id, user_id=user_id),
    )
    return {"synced": len(rows or [])}


@_inngest_client.create_function(
    fn_id="calendar-generate-briefs",
    trigger=inngest.TriggerCron(cron="5,20,35,50 * * * *"),  # 15-min offset from sync
    retries=0,
)
async def calendar_generate_briefs_cron(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    meetings = await step.run("list-due", meetings_needing_brief)
    if not meetings:
        return {"meetings": 0}
    for m in meetings:
        await step.send_event(
            f"emit-{m['id']}",
            inngest.Event(
                name="calendar/brief.meeting",
                data={"meeting_id": m["id"]},
                id=f"calbrief-{m['id']}",  # natural dedupe
            ),
        )
    return {"meetings": len(meetings)}


@_inngest_client.create_function(
    fn_id="calendar-brief-meeting",
    trigger=inngest.TriggerEvent(event="calendar/brief.meeting"),
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.meeting_id", scope="fn"),
    ],
    retries=2,
)
async def calendar_brief_meeting(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    meeting_id = ctx.event.data["meeting_id"]
    try:
        result = await step.run(
            f"brief-{meeting_id}",
            lambda: generate_meeting_prep_brief(meeting_id=meeting_id),
        )
        return {"meeting_id": meeting_id, "status": result.get("prep_brief_status")}
    except LookupError:
        log.info("calendar.brief.meeting_not_found id=%s", meeting_id)
        return {"meeting_id": meeting_id, "status": "not_found"}


FUNCTIONS = [
    calendar_sync_cron,
    calendar_sync_user,
    calendar_generate_briefs_cron,
    calendar_brief_meeting,
]
