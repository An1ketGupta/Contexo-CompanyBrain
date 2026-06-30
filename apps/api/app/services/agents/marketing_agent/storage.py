"""DB helpers for MarketingAgent. Service-role only — every caller is either
an Inngest worker or an admin-gated FastAPI handler that's already verified
the org / user."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.database import get_service_client


async def create_brief_row(
    *,
    org_id: str,
    created_by: str,
    run_id: str,
    objective: str,
    audience_hint: str | None,
    channels: list[str],
    competitors: list[str],
    collection_id: str | None,
) -> str:
    """Insert the brief row in 'generating' state. The agent fills it in
    artifact-by-artifact and flips status to 'ready' on completion."""
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "created_by": created_by,
        "run_id": run_id,
        "objective": objective,
        "audience_hint": audience_hint,
        "channels": channels,
        "competitors": competitors,
        "collection_id": collection_id,
        "status": "generating",
    }
    res = await asyncio.to_thread(
        lambda: svc.table("marketing_briefs").insert(row).execute()
    )
    return res.data[0]["id"]


async def fetch_brief(*, org_id: str, brief_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("marketing_briefs")
        .select("*")
        .eq("id", brief_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


async def list_briefs_for_org(
    *, org_id: str, status: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    svc = get_service_client()

    def _query() -> Any:
        q = (
            svc.table("marketing_briefs")
            .select("*")
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)
        return q.execute()

    res = await asyncio.to_thread(_query)
    return res.data or []


async def update_brief_row(*, brief_id: str, patch: dict[str, Any]) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("marketing_briefs").update(patch).eq("id", brief_id).execute()
    )


async def mark_brief_failed(*, brief_id: str, error: str) -> None:
    await update_brief_row(
        brief_id=brief_id,
        patch={"status": "failed", "error_message": (error or "")[:2000]},
    )


async def mark_brief_ready(
    *,
    brief_id: str,
    positioning: dict[str, Any],
    messaging_pillars: list[dict[str, Any]],
    competitive_angle: list[dict[str, Any]],
    channel_plan: list[dict[str, Any]],
    content_brief: dict[str, Any],
    sources: list[dict[str, Any]],
) -> None:
    await update_brief_row(
        brief_id=brief_id,
        patch={
            "status": "ready",
            "positioning": positioning,
            "messaging_pillars": messaging_pillars,
            "competitive_angle": competitive_angle,
            "channel_plan": channel_plan,
            "content_brief": content_brief,
            "sources": sources,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )


async def delete_brief(*, brief_id: str) -> None:
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("marketing_briefs").delete().eq("id", brief_id).execute()
    )
