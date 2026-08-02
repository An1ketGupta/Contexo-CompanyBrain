"""Scheduled-reports scheduling + report-data generation.

Two responsibilities split into pure functions:

1. `compute_next_send_at(...)` — converts a (frequency, day_of_*, hour_utc)
   spec into the next datetime the report should fire. No external deps
   (no `croniter`) — the supported schedules are simple enough that the
   manual arithmetic is clearer than a cron-string layer.

2. `gather_usage_summary(...)` / `gather_knowledge_health(...)` — pull the
   numbers an admin would want in an emailed report. Both reuse the same
   service-role aggregates the in-app analytics pages use, so what arrives
   by email matches what shows up in `/admin/analytics` exactly.

The Inngest dispatch cron in `app/inngest/report_functions.py` calls these
gather functions and enqueues an `email/send` event per recipient.
"""
from __future__ import annotations

import asyncio
import calendar
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)


# ── Next-send computation ───────────────────────────────────────────────────


def compute_next_send_at(
    *,
    frequency: str,
    send_time_utc: int,
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    after: datetime | None = None,
) -> datetime:
    """Return the next UTC datetime this report should fire at, strictly
    after `after` (default: now). All times are UTC; the field is named
    `send_time_utc` to make that explicit at every call site.

    Schedules:
        - daily       → every day at hour=send_time_utc
        - weekly      → every day_of_week (0=Mon … 6=Sun) at hour=send_time_utc
        - monthly     → day_of_month (1..28) at hour=send_time_utc.
                        We cap at 28 so February never skips a month.

    Edge cases:
        - If today's slot is already in the past, advance one cycle.
        - For monthly: if day_of_month > 28 we clamp to 28 (DB CHECK also
          enforces this; we belt-and-braces in code so a mis-typed migration
          doesn't crash the scheduler).
    """
    now = after or datetime.now(UTC)
    hour = max(0, min(23, int(send_time_utc)))

    if frequency == "daily":
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if frequency == "weekly":
        # Python's weekday(): Monday=0..Sunday=6, matches our column convention.
        dow = 0 if day_of_week is None else max(0, min(6, int(day_of_week)))
        candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        days_ahead = (dow - candidate.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    if frequency == "monthly":
        dom = 1 if day_of_month is None else max(1, min(28, int(day_of_month)))
        year, month = now.year, now.month
        # Try this month first.
        candidate = datetime(
            year, month, dom, hour, 0, 0, tzinfo=UTC
        )
        if candidate <= now:
            # Roll to next month, handling year wrap.
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            # In case dom > the # of days in the next month (only matters if
            # someone set dom>28; the cap above already prevents that, but if
            # the table CHECK is relaxed later we stay correct).
            last_dom = calendar.monthrange(year, month)[1]
            dom_clamped = min(dom, last_dom)
            candidate = datetime(
                year, month, dom_clamped, hour, 0, 0, tzinfo=UTC
            )
        return candidate

    # Unknown frequency — fall back to daily so nothing silently never sends.
    log.warning("scheduled_report_unknown_frequency", frequency=frequency)
    return compute_next_send_at(
        frequency="daily", send_time_utc=hour, after=now
    )


# ── Report data gatherers ────────────────────────────────────────────────────


def _last_n_days_iso(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


async def gather_usage_summary(org_id: str, *, window_days: int = 7) -> dict[str, Any]:
    """Roll up usage stats for the given org. Defaults to 7-day window so a
    weekly report has a natural "since last send" cadence. Daily reports
    pass window_days=1, monthly pass 30 — handled by the dispatcher.

    Same shape the existing weekly_digest_send_now uses but inlined here so
    scheduled reports are independent of the weekly cron's wiring.
    """
    svc = get_service_client()
    since = _last_n_days_iso(window_days)

    def _query() -> dict[str, Any]:
        org = (
            svc.table("organizations")
            .select("id, name, plan")
            .eq("id", org_id)
            .maybe_single()
            .execute()
            .data
            or {}
        )
        msgs = (
            svc.table("messages")
            .select("id, role, intent, confidence_score, feedback, time_saved_minutes")
            .eq("org_id", org_id)
            .eq("role", "assistant")
            .gte("created_at", since)
            .limit(50_000)
            .execute()
            .data
            or []
        )
        new_docs = (
            svc.table("documents")
            .select("id, name")
            .eq("org_id", org_id)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
            .data
            or []
        )
        active = (
            svc.table("conversations")
            .select("user_id")
            .eq("org_id", org_id)
            .gte("updated_at", since)
            .execute()
            .data
            or []
        )
        return {"org": org, "messages": msgs, "new_docs": new_docs, "active": active}

    raw = await asyncio.to_thread(_query)
    msgs = raw["messages"]
    new_docs = raw["new_docs"]

    time_saved_min = sum((m.get("time_saved_minutes") or 0) for m in msgs)
    pos = sum(1 for m in msgs if (m.get("feedback") or "").lower() in {"positive", "up"})
    neg = sum(1 for m in msgs if (m.get("feedback") or "").lower() in {"negative", "down"})
    low_conf = sum(
        1 for m in msgs
        if (m.get("confidence_score") is not None and float(m["confidence_score"]) < 5.0)
    )
    intent_counter = Counter(
        (m.get("intent") or "").strip() for m in msgs if (m.get("intent") or "").strip()
    )

    return {
        "org_name": raw["org"].get("name", "Your team"),
        "window_days": window_days,
        "query_count": len(msgs),
        "active_users": len({r["user_id"] for r in raw["active"] if r.get("user_id")}),
        "time_saved_minutes": round(time_saved_min, 1),
        "time_saved_hours": round(time_saved_min / 60, 1) if time_saved_min else 0,
        "positive_feedback_count": pos,
        "negative_feedback_count": neg,
        "low_confidence_count": low_conf,
        "new_document_count": len(new_docs),
        "new_document_titles": [d.get("name") for d in new_docs[:5] if d.get("name")],
        "top_intents": [
            {"intent": i, "count": c} for i, c in intent_counter.most_common(3)
        ],
    }


async def gather_knowledge_health(org_id: str) -> dict[str, Any]:
    """Knowledge-base health snapshot: corpus size, staleness, never-cited
    docs. Independent of any time window — it's a "state of the KB right
    now" report."""
    svc = get_service_client()
    since_30d = _last_n_days_iso(30)

    def _query() -> dict[str, Any]:
        org = (
            svc.table("organizations")
            .select("id, name")
            .eq("id", org_id)
            .maybe_single()
            .execute()
            .data
            or {}
        )
        total = (
            svc.table("documents")
            .select("id", count="exact", head=True)
            .eq("org_id", org_id)
            .eq("status", "ready")
            .execute()
            .count
            or 0
        )
        # Top-cited (active knowledge).
        top = (
            svc.table("documents")
            .select("id, name, citation_count")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .gt("citation_count", 0)
            .order("citation_count", desc=True)
            .limit(5)
            .execute()
            .data
            or []
        )
        # Stale: zero citations, older than 30 days.
        stale = (
            svc.table("documents")
            .select("id, name, created_at")
            .eq("org_id", org_id)
            .eq("status", "ready")
            .eq("citation_count", 0)
            .lt("created_at", since_30d)
            .order("created_at")
            .limit(10)
            .execute()
            .data
            or []
        )
        return {
            "org": org,
            "total_docs": total,
            "top_docs": top,
            "stale_docs": stale,
        }

    raw = await asyncio.to_thread(_query)

    return {
        "org_name": raw["org"].get("name", "Your team"),
        "total_docs": raw["total_docs"],
        "top_documents": [
            {"name": d.get("name"), "citations": int(d.get("citation_count") or 0)}
            for d in raw["top_docs"]
            if d.get("name")
        ],
        "stale_documents": [
            {"name": d.get("name")} for d in raw["stale_docs"] if d.get("name")
        ],
        "stale_count": len(raw["stale_docs"]),
    }


__all__ = [
    "compute_next_send_at",
    "gather_knowledge_health",
    "gather_usage_summary",
]
