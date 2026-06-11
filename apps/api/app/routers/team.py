"""Team-visible activity feed (V4 #57).

  GET /team/activity?limit=20  — recent activity from org members.

The feed is RLS-scoped on read: private rows for OTHER users are filtered
out by the activity_feed_select policy. We use the user-scoped client here
so the policy runs naturally; the response then joins user display names
via the service client (the users table has its own RLS limits).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization

log = logging.getLogger(__name__)

router = APIRouter(prefix="/team", tags=["team"])


@router.get("/activity")
async def get_team_activity(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(verify_jwt),
) -> list[dict[str, Any]]:
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")

    user_client = get_user_client(current_user["token"])
    feed_res = await asyncio.to_thread(
        lambda: user_client.table("activity_feed")
        .select("id, user_id, activity_type, summary, metadata, created_at")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    feed = feed_res.data or []
    if not feed:
        return []

    # Resolve user display names via service client. RLS on `users` restricts
    # the user-scoped client to the caller's own row; we want everyone in the
    # org for the avatar/name. Service-role read is org-bounded by the input
    # set (already filtered to this org via activity_feed RLS).
    user_ids = list({row["user_id"] for row in feed if row.get("user_id")})
    users_by_id: dict[str, dict[str, Any]] = {}
    if user_ids:
        svc = get_service_client()
        users_res = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name, email")
            .in_("id", user_ids)
            .execute()
        )
        for row in (users_res.data or []):
            users_by_id[row["id"]] = row

    out: list[dict[str, Any]] = []
    for row in feed:
        u = users_by_id.get(row.get("user_id"), {})
        name = u.get("display_name") or (u.get("email") or "Someone").split("@")[0]
        out.append({
            "id": row["id"],
            "user_id": row.get("user_id"),
            "user_name": name,
            "user_email": u.get("email"),
            "activity_type": row["activity_type"],
            "summary": row["summary"],
            "metadata": row.get("metadata") or {},
            "created_at": row["created_at"],
        })
    return out
