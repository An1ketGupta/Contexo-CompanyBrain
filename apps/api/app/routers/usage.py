"""Usage snapshot endpoint — powers the sidebar quota meter.

Also hosts the Day-12 admin knowledge-intelligence dashboard endpoint, which
joins the chunk_citations analytics with the documents table to surface
top-cited and never-cited documents per org.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services.rate_limit import get_usage_snapshot

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/me")
async def get_my_usage(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id = current_user.get("org_id")
    if not org_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")

    snap = await get_usage_snapshot(org_id)
    reset_at = datetime.now(UTC) + timedelta(seconds=snap.seconds_until_reset)
    return {
        "plan": snap.plan,
        "used": snap.used,
        "limit": snap.limit,
        "reset_at": reset_at.isoformat(),
        "seconds_until_reset": snap.seconds_until_reset,
        # `unlimited` is a UX hint — `limit=null` already implies it but the
        # meter renders differently (no bar) and switching on `null` reads worse.
        "unlimited": snap.limit is None,
        "source": snap.source,
    }


# ── Admin: Knowledge Intelligence (Day 12 / #49) ─────────────────────────────

@router.get("/knowledge-intelligence")
async def get_knowledge_intelligence(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Top-cited + never-cited documents, plus org-level totals.

    Admin-only. We check role using a user-scoped client so RLS on the users
    table fires — only an admin row passes the filter. The actual data reads
    use the service client because chunk_citations is org-scoped but the
    aggregate stats RPC is SECURITY DEFINER (see migration 014).
    """
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
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin only.",
        )

    svc = get_service_client()

    top_docs_task = asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_type, citation_count, last_cited_at, created_at")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .gt("citation_count", 0)
        .order("citation_count", desc=True)
        .limit(10)
        .execute()
    )
    unused_docs_task = asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, name, file_type, citation_count, created_at")
        .eq("org_id", org_id)
        .eq("status", "ready")
        .eq("citation_count", 0)
        .order("created_at")  # oldest first — most likely actually stale
        .limit(10)
        .execute()
    )
    aggregates_task = asyncio.to_thread(
        lambda: svc.rpc("knowledge_intelligence", {"target_org_id": org_id}).execute()
    )

    top_docs_res, unused_docs_res, agg_res = await asyncio.gather(
        top_docs_task, unused_docs_task, aggregates_task
    )

    agg_data = (agg_res.data or [None])[0] or {}

    return {
        "top_documents": top_docs_res.data or [],
        "unused_documents": unused_docs_res.data or [],
        "totals": {
            "documents": int(agg_data.get("total_documents") or 0),
            "ready_documents": int(agg_data.get("ready_documents") or 0),
            "cited_documents": int(agg_data.get("cited_documents") or 0),
            "citations": int(agg_data.get("total_citations") or 0),
        },
    }
