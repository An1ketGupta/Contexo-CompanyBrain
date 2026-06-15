"""Compliance reminder pipeline (Agent Roadmap Day 10).

Two Inngest functions:

  1. `compliance-daily-reminders` (cron 09:00 UTC):
       Fans out per-org. For each org we honour:
         * compliance.reminder_threshold_days  — min age before first ping
         * compliance.max_reminders            — hard cap per (user, doc)
         * compliance.reminder_cadence_days    — min gap between reminders
       Each (user, org) gets one consolidated email listing every pending
       doc, so an admin who uploads 5 policies in a week doesn't spam every
       user 5 times. Reminder_count + last_reminder_at are incremented in
       a single batch update.

  2. `compliance-reminder-now` (event-driven):
       The /compliance/admin/{doc}/remind-now button fires
       `compliance/reminder-now`. Bypasses the threshold-days gate but still
       respects max_reminders. Useful for compliance deadlines.

Both reuse `send_email_event` so retries, dedupe, and the email_events
audit trail come for free. Dedupe keys include the day so re-runs of the
cron in the same day are no-ops.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import inngest

from app.config import get_settings
from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.email import send_email_event

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# ── Config helpers ────────────────────────────────────────────────────────


def _compliance_config(org_metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Pull compliance config out of organizations.metadata with defaults."""
    meta = org_metadata or {}
    cfg = meta.get("compliance") or {}
    return {
        "reminder_threshold_days": int(cfg.get("reminder_threshold_days") or 3),
        "max_reminders": int(cfg.get("max_reminders") or 3),
        "reminder_cadence_days": int(cfg.get("reminder_cadence_days") or 1),
    }


# ── Daily cron ────────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="compliance-daily-reminders",
    # 09:00 UTC daily. The user-facing window varies by tz but most teams
    # see the email before lunch in NA / late afternoon in EU, which is
    # roughly the sweet spot for "I'll do this now" vs "I'll do this later".
    trigger=inngest.TriggerCron(cron="0 9 * * *"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=1)],
)
async def compliance_daily_reminders(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    org_ids = await step.run("collect-orgs-with-pending", _collect_orgs_with_pending)
    sent_total = 0
    for org_id in org_ids:
        n = await step.run(
            f"remind-org-{org_id}",
            lambda oid=org_id, d=today: _send_org_reminders(org_id=oid, today=d),
        )
        sent_total += int(n or 0)
    return {"orgs": len(org_ids), "users_reminded": sent_total}


@_inngest_client.create_function(
    fn_id="compliance-reminder-now",
    trigger=inngest.TriggerEvent(event="compliance/reminder-now"),
    retries=1,
)
async def compliance_reminder_now(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    document_id: str | None = data.get("document_id")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    n = await ctx.step.run(
        "remind-now",
        lambda: _send_org_reminders(
            org_id=org_id,
            today=today,
            document_id=document_id,
            ignore_threshold=True,
        ),
    )
    return {"users_reminded": int(n or 0)}


# ── Helpers ───────────────────────────────────────────────────────────────


async def _collect_orgs_with_pending() -> list[str]:
    """Return org_ids that have ANY pending acknowledgement.

    Cheaper than fanning out across every org — most orgs on any given day
    have zero pending rows.
    """
    svc = get_service_client()

    def _q() -> list[str]:
        res = (
            svc.table("acknowledgements")
            .select("org_id")
            .eq("status", "pending")
            .limit(50000)
            .execute()
        )
        return sorted({row["org_id"] for row in (res.data or []) if row.get("org_id")})

    return await asyncio.to_thread(_q)


async def _send_org_reminders(
    *,
    org_id: str,
    today: str,
    document_id: str | None = None,
    ignore_threshold: bool = False,
) -> int:
    """Compute eligible reminders for an org and dispatch them.

    Returns the number of users notified (one email per user, regardless of
    pending doc count). Idempotent per (user, day) via the dedupe_key.
    """
    svc = get_service_client()
    settings = get_settings()

    # Pull the org's compliance config.
    org_res = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("id, name, metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    org = (org_res.data or {}) if org_res else {}
    if not org:
        return 0
    cfg = _compliance_config(org.get("metadata"))
    threshold_days = cfg["reminder_threshold_days"]
    max_reminders = cfg["max_reminders"]
    cadence_days = cfg["reminder_cadence_days"]

    now = datetime.now(timezone.utc)
    threshold_cutoff = (now - timedelta(days=threshold_days)).isoformat()
    cadence_cutoff = (now - timedelta(days=cadence_days)).isoformat()

    # Pull pending acks for the org. We could push the filters into Postgres
    # via .lte / .lt — and we do, but we still post-filter cadence_cutoff
    # because PostgREST can't express "last_reminder_at IS NULL OR <X"
    # cleanly in one .or_() with a server-side date arithmetic. The size
    # here is bounded by org_users × policy_docs which stays small.
    q = (
        svc.table("acknowledgements")
        .select(
            "id, user_id, document_id, reminder_count, last_reminder_at, "
            "created_at, documents(name)"
        )
        .eq("org_id", org_id)
        .eq("status", "pending")
        .lt("reminder_count", max_reminders)
    )
    if not ignore_threshold:
        q = q.lte("created_at", threshold_cutoff)
    if document_id:
        q = q.eq("document_id", document_id)

    acks_res = await asyncio.to_thread(lambda: q.limit(20000).execute())
    rows = acks_res.data or []
    if not rows:
        return 0

    # Honour cadence_days for re-reminders (skip rows reminded in last cadence).
    eligible: list[dict[str, Any]] = []
    for r in rows:
        last = r.get("last_reminder_at")
        if last and last > cadence_cutoff:
            continue
        eligible.append(r)
    if not eligible:
        return 0

    # Group by user — one email per user listing all their pending docs.
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        uid = r.get("user_id")
        if uid:
            by_user[uid].append(r)

    # Resolve emails + display names for the involved users.
    user_ids = list(by_user.keys())
    profiles: dict[str, dict[str, Any]] = {}
    if user_ids:
        prof_res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", user_ids)
            .execute()
        )
        for u in (prof_res.data or []):
            profiles[u["id"]] = {"display_name": u.get("display_name")}
        for uid in user_ids:
            try:
                au = await asyncio.to_thread(lambda u=uid: svc.auth.admin.get_user_by_id(u))
                email = getattr(getattr(au, "user", None), "email", None)
                if email:
                    profiles.setdefault(uid, {}).update({"email": email})
            except Exception:
                continue

    sent = 0
    ids_to_bump: list[str] = []
    nowiso = now.isoformat()
    org_name = org.get("name") or "Your team"
    app_url = settings.app_url.rstrip("/")

    for uid, acks in by_user.items():
        profile = profiles.get(uid) or {}
        email = profile.get("email")
        if not email:
            continue
        first_name = (profile.get("display_name") or "").strip().split(" ")[0] or "there"
        pending_docs = [
            {
                "name": (a.get("documents") or {}).get("name") or "Untitled document",
                "document_id": a.get("document_id"),
                "url": f"{app_url}/compliance/pending",
            }
            for a in acks
        ]
        try:
            await send_email_event(
                event_type="acknowledgement_reminder",
                to=email,
                user_id=uid,
                org_id=org_id,
                # One reminder email per (user, day). Re-firing the cron in
                # the same day is a no-op thanks to the email_events index.
                dedupe_key=f"{today}-{document_id or 'daily'}",
                data={
                    "first_name": first_name,
                    "org_name": org_name,
                    "pending_docs": pending_docs,
                    "pending_count": len(pending_docs),
                    "app_url": app_url,
                },
            )
            sent += 1
            ids_to_bump.extend(a["id"] for a in acks if a.get("id"))
        except Exception as exc:
            log.warning(
                "compliance_reminder_dispatch_failed",
                org_id=org_id,
                user_id=uid,
                error=str(exc),
            )

    # Bump reminder_count + last_reminder_at in a single batch. We use the
    # service client + .in_() because user-scoped RLS would block writes
    # across users.
    if ids_to_bump:
        # PostgREST doesn't support `col = col + 1` in .update() — we read
        # then write per row. The list is short (≤ org_size × pending_docs),
        # and we already paid for the read above so this is fast.
        try:
            for ack_id in ids_to_bump:
                row = next((a for a in eligible if a.get("id") == ack_id), None)
                if not row:
                    continue
                await asyncio.to_thread(
                    lambda i=ack_id, c=(row.get("reminder_count") or 0): svc.table("acknowledgements")
                    .update(
                        {
                            "reminder_count": int(c) + 1,
                            "last_reminder_at": nowiso,
                        }
                    )
                    .eq("id", i)
                    .execute()
                )
        except Exception as exc:
            log.warning("compliance_reminder_bump_failed", org_id=org_id, error=str(exc))

    return sent


FUNCTIONS = [compliance_daily_reminders, compliance_reminder_now]
