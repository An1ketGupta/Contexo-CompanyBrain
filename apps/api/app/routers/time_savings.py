"""V5 #73 — Time-savings rollup endpoints.

Two surfaces:

  GET /me/time-savings    — caller's personal totals (week / month / all-time)
  GET /admin/time-savings — org-wide totals + per-user breakdown (admin only)

Both endpoints SUM the new `messages.time_saved_minutes` column. Per-user
attribution joins through `conversations.user_id` because messages don't
carry user_id directly — the same convention used by /admin/analytics.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization

log = logging.getLogger(__name__)

router = APIRouter(tags=["time-savings"])


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only.")


async def _sum_for_conversations(
    *, org_id: str, conv_ids: list[str], cutoff: datetime
) -> int:
    """SUM messages.time_saved_minutes across a list of conversation IDs."""
    if not conv_ids:
        return 0
    svc = get_service_client()
    total = 0
    # Supabase's `in_` accepts up to ~1000 ids per call — chunk to stay safe.
    for start in range(0, len(conv_ids), 500):
        chunk = conv_ids[start : start + 500]
        res = await asyncio.to_thread(
            lambda c=chunk: svc.table("messages")
            .select("time_saved_minutes")
            .eq("org_id", org_id)
            .eq("role", "assistant")
            .in_("conversation_id", c)
            .gte("created_at", cutoff.isoformat())
            .limit(50000)
            .execute()
        )
        total += sum(int(r.get("time_saved_minutes") or 0) for r in (res.data or []))
    return total


async def _convs_for_user(*, org_id: str, user_id: str) -> list[str]:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("conversations")
        .select("id")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .limit(10000)
        .execute()
    )
    return [row["id"] for row in (res.data or []) if row.get("id")]


@router.get("/me/time-savings")
async def my_time_savings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _token = _require_org(current_user)
    now = datetime.now(UTC)

    convs = await _convs_for_user(org_id=org_id, user_id=user_id)
    if not convs:
        return {"this_week": 0, "this_month": 0, "all_time": 0, "hours_this_month": 0.0}

    week_total, month_total, all_time = await asyncio.gather(
        _sum_for_conversations(org_id=org_id, conv_ids=convs, cutoff=now - timedelta(days=7)),
        _sum_for_conversations(org_id=org_id, conv_ids=convs, cutoff=now - timedelta(days=30)),
        # Epoch — pick anything safely older than any plausible signup.
        _sum_for_conversations(org_id=org_id, conv_ids=convs, cutoff=datetime(2020, 1, 1, tzinfo=UTC)),
    )
    return {
        "this_week": week_total,
        "this_month": month_total,
        "all_time": all_time,
        "hours_this_month": round(month_total / 60, 1),
    }


@router.get("/admin/time-savings")
async def org_time_savings(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=30)
    svc = get_service_client()

    # Org-wide sum: assistant rows in the window.
    res = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("time_saved_minutes, conversation_id")
        .eq("org_id", org_id)
        .eq("role", "assistant")
        .gte("created_at", cutoff.isoformat())
        .limit(100_000)
        .execute()
    )
    rows = res.data or []
    total_minutes = sum(int(r.get("time_saved_minutes") or 0) for r in rows)

    # Per-user attribution — bucket by user_id via the conversation join.
    conv_ids = list({r["conversation_id"] for r in rows if r.get("conversation_id")})
    conv_to_user: dict[str, str] = {}
    for start in range(0, len(conv_ids), 800):
        chunk = conv_ids[start : start + 800]
        cres = await asyncio.to_thread(
            lambda c=chunk: svc.table("conversations")
            .select("id, user_id")
            .in_("id", c)
            .execute()
        )
        for row in (cres.data or []):
            if row.get("user_id"):
                conv_to_user[row["id"]] = row["user_id"]

    by_user: dict[str, int] = {}
    for r in rows:
        uid = conv_to_user.get(r.get("conversation_id"))
        if not uid:
            continue
        by_user[uid] = by_user.get(uid, 0) + int(r.get("time_saved_minutes") or 0)

    # Top 25 contributors by minutes, with display names.
    ranked = sorted(by_user.items(), key=lambda kv: kv[1], reverse=True)[:25]
    name_lookup: dict[str, str] = {}
    if ranked:
        ids = [uid for uid, _ in ranked]
        users_res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", ids)
            .execute()
        )
        for u in (users_res.data or []):
            name_lookup[u["id"]] = u.get("display_name") or "Teammate"

    per_user = [
        {
            "user_id": uid,
            "name": name_lookup.get(uid) or "Teammate",
            "minutes": minutes,
            "hours": round(minutes / 60, 1),
        }
        for uid, minutes in ranked
    ]

    return {
        "total_minutes_this_month": total_minutes,
        "total_hours_this_month": round(total_minutes / 60, 1),
        "per_user": per_user,
    }
