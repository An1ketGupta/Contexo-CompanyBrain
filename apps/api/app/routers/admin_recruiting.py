"""Admin recruiting dashboard.

Surfaces hiring throughput, ATS distribution, time-to-publish, grounding rate,
top recruiters, and the audit log tail. Sits next to /admin/analytics in the
admin nav. Service-role aggregations because we read across the whole org.

  GET /admin/recruiting?period=7d|30d|90d
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.core.rate_limiter import admin_analytics_limiter
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}


async def _require_admin(current_user: dict) -> str:
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    user_client = get_user_client(current_user["token"])
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", current_user["user_id"])
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin only."
        )
    return org_id


def _time_to_publish_seconds(row: dict[str, Any]) -> int | None:
    """Compute seconds from created_at to published_at. Returns None for
    not-yet-published or malformed timestamps."""
    if not row.get("published_at"):
        return None
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        published = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        delta = (published - created).total_seconds()
        return int(delta) if delta >= 0 else None
    except (KeyError, ValueError, TypeError):
        return None


@router.get("/recruiting")
async def get_recruiting_analytics(
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(admin_analytics_limiter),
) -> dict[str, Any]:
    org_id = await _require_admin(current_user)
    days = _PERIOD_DAYS[period]
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    svc = get_service_client()

    # Pull all rows in the period — even a year of high-volume hiring at scale
    # is well under 10k rows for one org, so a single round-trip is fine.
    reqs_res = await asyncio.to_thread(
        lambda: svc.table("job_requisitions")
        .select(
            "id, created_by, role_request, status, ats_platform, grounded, "
            "created_at, published_at, error_message"
        )
        .eq("org_id", org_id)
        .gte("created_at", cutoff.isoformat())
        .order("created_at", desc=True)
        .limit(2000)
        .execute()
    )
    rows: list[dict[str, Any]] = reqs_res.data or []

    total = len(rows)
    by_status: dict[str, int] = {}
    by_ats: dict[str, int] = {}
    grounded_count = 0
    creator_counts: dict[str, int] = {}
    publish_durations: list[int] = []

    for r in rows:
        # Normalise legacy 'Published' rows that pre-date migration 072.
        raw_status = (r.get("status") or "draft")
        s = "published" if raw_status.lower() == "published" else raw_status
        by_status[s] = by_status.get(s, 0) + 1
        if r.get("ats_platform"):
            by_ats[r["ats_platform"]] = by_ats.get(r["ats_platform"], 0) + 1
        if r.get("grounded"):
            grounded_count += 1
        cb = r.get("created_by")
        if cb:
            creator_counts[cb] = creator_counts.get(cb, 0) + 1
        ttp = _time_to_publish_seconds(r)
        if ttp is not None:
            publish_durations.append(ttp)

    # Resolve creator names. Bounded to top 10 to keep the payload tight.
    top_creator_ids = sorted(creator_counts.items(), key=lambda kv: -kv[1])[:10]
    creator_lookup: dict[str, dict[str, Any]] = {}
    if top_creator_ids:
        ids = [cid for cid, _ in top_creator_ids]
        # Email lives in auth.users, not the profile table — resolve names
        # from `users` and cross-look emails via the service-role admin API.
        # Failures on either side are tolerated so a deleted user never
        # crashes the dashboard.
        users_res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", ids)
            .execute()
        )
        for u in users_res.data or []:
            creator_lookup[u["id"]] = {"display_name": u.get("display_name")}

        for cid in ids:
            try:
                au = await asyncio.to_thread(
                    lambda i=cid: svc.auth.admin.get_user_by_id(i)
                )
                email = getattr(getattr(au, "user", None), "email", None)
            except Exception:
                email = None
            creator_lookup.setdefault(cid, {})["email"] = email

    top_recruiters = [
        {
            "user_id": cid,
            "name": (creator_lookup.get(cid) or {}).get("display_name") or "Unknown",
            "email": (creator_lookup.get(cid) or {}).get("email"),
            "requisitions": count,
        }
        for cid, count in top_creator_ids
    ]

    # Daily counts for the bar chart. Bucket on UTC date so DST changes don't
    # split a day into two buckets.
    daily_counts: dict[str, int] = {}
    for r in rows:
        try:
            day = datetime.fromisoformat(
                r["created_at"].replace("Z", "+00:00")
            ).date().isoformat()
        except (KeyError, ValueError, TypeError):
            continue
        daily_counts[day] = daily_counts.get(day, 0) + 1
    daily_series = [
        {"day": d, "count": c}
        for d, c in sorted(daily_counts.items())
    ]

    # Audit log tail — last 20 events. Admins use this to verify what got
    # posted where on a support call.
    audit_res = await asyncio.to_thread(
        lambda: svc.table("recruiting_audit_log")
        .select(
            "id, requisition_id, action, status, ats_platform, "
            "status_code, error_message, duration_ms, created_at"
        )
        .eq("org_id", org_id)
        .gte("created_at", cutoff.isoformat())
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    )

    median_ttp = int(median(publish_durations)) if publish_durations else None

    return {
        "period": period,
        "stats": {
            "total_requisitions": total,
            "published": by_status.get("published", 0),
            "draft": by_status.get("draft", 0),
            "failed": by_status.get("failed", 0),
            "grounded_rate": round(grounded_count / total, 3) if total else 0.0,
            "median_seconds_to_publish": median_ttp,
        },
        "by_ats": [
            {"platform": p, "count": c}
            for p, c in sorted(by_ats.items(), key=lambda kv: -kv[1])
        ],
        "daily_series": daily_series,
        "top_recruiters": top_recruiters,
        "recent_audit": audit_res.data or [],
    }
