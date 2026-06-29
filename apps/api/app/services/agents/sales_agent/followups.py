"""Stage 3 / 7 — cron-driven finders for follow-ups and check-ins.

Two tiny SQL helpers fired by the daily Inngest cron. The drafting itself
lives in `outreach.py`; this module is just the "what deals are due right
now?" query layer plus the policy constants (cadence, max sends).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_service_client

log = logging.getLogger(__name__)


# Cadence policy — kept here so the cron and the agent reach the same
# conclusion when they read it. Document in Sales_Agent.md before changing.
FOLLOWUP_INTERVAL_DAYS = 3
MAX_FOLLOWUPS = 3
CHECKIN_INTERVAL_DAYS = 4
MAX_CHECKINS = 2


def _now() -> datetime:
    return datetime.now(UTC)


# ── Follow-ups ──────────────────────────────────────────────────────────────


async def find_deals_due_for_followup(*, limit: int = 100) -> list[dict[str, Any]]:
    """Deals in `awaiting_reply` whose next_followup_due_at is past and have
    not exhausted the cadence yet. Only returns the lightweight subset the
    cron needs to fan-out events from."""
    svc = get_service_client()
    now_iso = _now().isoformat()

    def _q() -> list[dict[str, Any]]:
        res = (
            svc.table("deal_runs")
            .select(
                "id, org_id, created_by, company_name, contact_name, contact_title, "
                "contact_email, outreach_subject, outreach_email_body, outreach_sent_at, "
                "followup_count, next_followup_due_at, status"
            )
            .eq("status", "awaiting_reply")
            .lte("next_followup_due_at", now_iso)
            .lt("followup_count", MAX_FOLLOWUPS)
            .order("next_followup_due_at", desc=False)
            .limit(limit)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_q)


async def find_deals_due_for_close_no_reply(*, limit: int = 100) -> list[dict[str, Any]]:
    """Deals where we've sent MAX_FOLLOWUPS and the latest follow-up is older
    than FOLLOWUP_INTERVAL_DAYS — close them out as no_reply_closed."""
    svc = get_service_client()
    cutoff = (_now() - timedelta(days=FOLLOWUP_INTERVAL_DAYS)).isoformat()

    def _q() -> list[dict[str, Any]]:
        res = (
            svc.table("deal_runs")
            .select("id, org_id, created_by, company_name, followup_count, last_followup_at, status")
            .eq("status", "awaiting_reply")
            .gte("followup_count", MAX_FOLLOWUPS)
            .lte("last_followup_at", cutoff)
            .limit(limit)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_q)


# ── Check-ins (post-proposal) ────────────────────────────────────────────────


async def find_deals_due_for_checkin(*, limit: int = 100) -> list[dict[str, Any]]:
    svc = get_service_client()
    now_iso = _now().isoformat()

    def _q() -> list[dict[str, Any]]:
        res = (
            svc.table("deal_runs")
            .select(
                "id, org_id, created_by, company_name, contact_name, contact_email, "
                "proposal_sent_at, checkin_count, next_checkin_due_at, status"
            )
            .eq("status", "awaiting_decision")
            .lte("next_checkin_due_at", now_iso)
            .lt("checkin_count", MAX_CHECKINS)
            .order("next_checkin_due_at", desc=False)
            .limit(limit)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_q)


async def find_deals_at_risk(*, limit: int = 100) -> list[dict[str, Any]]:
    """Deals where check-ins are exhausted and the most recent one is past
    cadence — flip to at_risk so the rep sees a clear "needs attention" badge."""
    svc = get_service_client()
    cutoff = (_now() - timedelta(days=CHECKIN_INTERVAL_DAYS)).isoformat()

    def _q() -> list[dict[str, Any]]:
        res = (
            svc.table("deal_runs")
            .select("id, org_id, created_by, company_name, checkin_count, last_checkin_at, status")
            .eq("status", "awaiting_decision")
            .gte("checkin_count", MAX_CHECKINS)
            .lte("last_checkin_at", cutoff)
            .limit(limit)
            .execute()
        )
        return res.data or []

    return await asyncio.to_thread(_q)


# ── Helpers used when persisting follow-up / check-in send state ──────────


def schedule_next_followup(followup_count: int) -> datetime | None:
    """The agent calls this AFTER incrementing followup_count to figure out
    when to look for the next one. Returns None when we've exhausted the
    cadence — the daily cron will then close the deal."""
    if followup_count >= MAX_FOLLOWUPS:
        return None
    return _now() + timedelta(days=FOLLOWUP_INTERVAL_DAYS)


def schedule_next_checkin(checkin_count: int) -> datetime | None:
    if checkin_count >= MAX_CHECKINS:
        return None
    return _now() + timedelta(days=CHECKIN_INTERVAL_DAYS)
