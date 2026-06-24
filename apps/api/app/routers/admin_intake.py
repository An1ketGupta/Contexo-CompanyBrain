"""Admin endpoints for Day-2 document-intake intelligence.

Two surfaces:
  * Duplicates — list pending duplicate-detected notifications + the matches.
  * Routing — list pending routing_suggestions, accept/reject in bulk.

Backfill triggers:
  * POST /admin/duplicates/backfill           one-shot summary-embedding fill
  * POST /admin/routing/run                   force a routing pass on N recent docs

These are admin-only, mounted at /admin/{duplicates,routing}.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin-intake"])


# ── Shared helpers (mirror collections.py pattern) ───────────────────────


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only.")


# ── Duplicates ───────────────────────────────────────────────────────────


@router.get("/admin/duplicates")
async def list_duplicates(
    current_user: dict = Depends(verify_jwt),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List recent duplicate-detected notifications for the org.

    We surface the notification rows directly (which already carry the match
    list in their metadata) rather than maintaining a parallel duplicates
    table. Single source of truth = the notification system.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        # Service-role read so we see all admin notifications across the org,
        # not just the caller's. The admin check above gates access.
        res = (
            svc.table("notifications")
            .select("id, user_id, title, body, metadata, link_url, read_at, created_at")
            .eq("org_id", org_id)
            .eq("type", "duplicate_document_detected")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch)

    # Collapse duplicates of the same dedupe pair (multiple admins get the
    # same notification) into one display row.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in rows:
        meta = r.get("metadata") or {}
        pair = f"{meta.get('document_id')}:{meta.get('match_document_id')}"
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(
            {
                "id": r["id"],
                "title": r["title"],
                "body": r["body"],
                "document_id": meta.get("document_id"),
                "match_document_id": meta.get("match_document_id"),
                "similarity": meta.get("similarity"),
                "matches": meta.get("matches") or [],
                "created_at": r["created_at"],
            }
        )
    return {"duplicates": deduped}


@router.post("/admin/duplicates/backfill", status_code=status.HTTP_202_ACCEPTED)
async def run_backfill(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Fire the one-shot summary-embedding backfill Inngest function.

    Idempotent: a second call returns immediately if a backfill is already in
    flight (Inngest's function-level concurrency=1 handles this).
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="document/backfill.summary-embeddings",
            data={"requested_by": user_id, "org_id": org_id},
        )
    )
    return {"status": "queued"}


@router.delete("/admin/duplicates/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_duplicate(
    notification_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    """Mark the duplicate notification as read for ALL admins in the org.

    We treat dismissal as an org-level decision: once one admin reviews and
    dismisses a duplicate, the rest shouldn't see it in their queue.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _run() -> None:
        # Lookup the dedupe_key on the row, then mark every notification with
        # the same key in this org as read.
        target = (
            svc.table("notifications")
            .select("dedupe_key")
            .eq("id", notification_id)
            .eq("org_id", org_id)
            .eq("type", "duplicate_document_detected")
            .maybe_single()
            .execute()
        )
        if not target or not target.data:
            return
        dedupe = target.data.get("dedupe_key")
        if not dedupe:
            svc.table("notifications").update({"read_at": "now()"}).eq(
                "id", notification_id
            ).execute()
            return
        svc.table("notifications").update({"read_at": "now()"}).eq("org_id", org_id).eq(
            "type", "duplicate_document_detected"
        ).eq("dedupe_key", dedupe).execute()

    await asyncio.to_thread(_run)


# ── Routing suggestions ─────────────────────────────────────────────────


class RoutingDecision(BaseModel):
    suggestion_ids: list[str] = Field(default_factory=list, max_length=100)


@router.get("/admin/routing")
async def list_routing_suggestions(
    current_user: dict = Depends(verify_jwt),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> list[dict[str, Any]]:
        # Join through to document name + collection name for the admin UI.
        # We don't use a SQL view — keeps schema small, and the per-page row
        # count is < 200 so two extra lookups per page is fine.
        rows = (
            svc.table("routing_suggestions")
            .select(
                "id, document_id, collection_id, suggested_tag, similarity, status, created_at"
            )
            .eq("org_id", org_id)
            .eq("status", "pending")
            .order("similarity", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        if not rows:
            return []

        doc_ids = list({r["document_id"] for r in rows})
        col_ids = list({r["collection_id"] for r in rows})

        docs = (
            svc.table("documents")
            .select("id, name")
            .in_("id", doc_ids)
            .execute()
            .data
            or []
        )
        cols = (
            svc.table("collections")
            .select("id, name, color, icon")
            .in_("id", col_ids)
            .execute()
            .data
            or []
        )
        doc_map = {d["id"]: d for d in docs}
        col_map = {c["id"]: c for c in cols}

        for r in rows:
            r["document_name"] = (doc_map.get(r["document_id"]) or {}).get("name")
            r["collection_name"] = (col_map.get(r["collection_id"]) or {}).get("name")
            r["collection_color"] = (col_map.get(r["collection_id"]) or {}).get("color")
            r["collection_icon"] = (col_map.get(r["collection_id"]) or {}).get("icon")
        return rows

    rows = await asyncio.to_thread(_fetch)
    return {"suggestions": rows}


@router.post("/admin/routing/accept")
async def accept_routing(
    body: RoutingDecision,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    from app.services.smart_routing import accept_suggestion

    results: list[dict[str, Any]] = []
    for sid in body.suggestion_ids:
        r = await accept_suggestion(suggestion_id=sid, accepted_by=user_id)
        results.append({"suggestion_id": sid, **(r or {})})
    return {"accepted": results}


@router.post("/admin/routing/reject")
async def reject_routing(
    body: RoutingDecision,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    from app.services.smart_routing import reject_suggestion

    results: list[dict[str, Any]] = []
    for sid in body.suggestion_ids:
        r = await reject_suggestion(suggestion_id=sid, rejected_by=user_id)
        results.append({"suggestion_id": sid, **(r or {})})
    return {"rejected": results}
