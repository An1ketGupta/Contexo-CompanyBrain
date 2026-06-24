"""Meeting summaries — viewer surface for MeetingNotesAgent output.

The agent (Agent Day 13) extracts structured info from uploaded transcripts
into the `meeting_summaries` table. The documents list already surfaces both
the source transcript and the derived "Meeting summary" doc, but neither
exposes the structured artefacts (attendees, decisions, action items) in
their native shape. This router is the surface that does.

Surface:
  GET  /meetings                        — list summaries for the org
  GET  /meetings/{summary_id}           — full structured detail
  POST /meetings/{summary_id}/reprocess — re-fire the agent (admin only)

All reads are org-scoped via RLS-equivalent application checks against
get_service_client(); writes (reprocess) gate on the admin role. Members
can read every summary because the source documents are already org-wide
readable — restricting the viewer would just hide what's already visible
through the docs list.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import verify_jwt
from app.database import get_service_client
from app.inngest import get_inngest_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/meetings", tags=["meetings"])


class MeetingSummaryRow(BaseModel):
    id: str
    org_id: str
    source_document_id: str
    derived_document_id: str | None
    source_format: str
    meeting_started_at: str | None
    meeting_duration_seconds: int | None
    summary: str | None
    attendee_count: int
    decision_count: int
    action_item_count: int
    slack_posted_at: str | None
    created_at: str
    source_document_name: str | None


@router.get("")
async def list_meeting_summaries(
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """List meeting summaries for the org with compact counts.

    Heavy fields (attendees, decisions, action_items, transcript dialog) are
    omitted; fetched on demand by the detail endpoint so the list stays
    cheap to render for orgs with hundreds of meetings.
    """
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization found.")

    svc = get_service_client()

    def _query() -> Any:
        return (
            svc.table("meeting_summaries")
            .select(
                "id, org_id, source_document_id, derived_document_id, "
                "source_format, meeting_started_at, meeting_duration_seconds, "
                "summary, attendees, decisions, action_items, slack_posted_at, "
                "created_at",
                count="exact",
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )

    res = await asyncio.to_thread(_query)
    rows = res.data or []

    # Hydrate source document names. One bulk read is cheaper than a join
    # for the typical page size (≤30).
    doc_ids = [r["source_document_id"] for r in rows if r.get("source_document_id")]
    name_map: dict[str, str] = {}
    if doc_ids:
        docs = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, name")
            .in_("id", doc_ids)
            .execute()
        )
        name_map = {d["id"]: d.get("name") or "" for d in (docs.data or [])}

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "org_id": r["org_id"],
                "source_document_id": r.get("source_document_id"),
                "derived_document_id": r.get("derived_document_id"),
                "source_format": r.get("source_format") or "unknown",
                "meeting_started_at": r.get("meeting_started_at"),
                "meeting_duration_seconds": r.get("meeting_duration_seconds"),
                "summary": r.get("summary"),
                "attendee_count": len(r.get("attendees") or []),
                "decision_count": len(r.get("decisions") or []),
                "action_item_count": len(r.get("action_items") or []),
                "slack_posted_at": r.get("slack_posted_at"),
                "created_at": r["created_at"],
                "source_document_name": name_map.get(r.get("source_document_id") or ""),
            }
        )
    return {
        "summaries": out,
        "total": res.count or len(out),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{summary_id}")
async def get_meeting_summary(
    summary_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Full structured view including attendees, decisions, action items."""
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization found.")

    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("meeting_summaries")
        .select("*")
        .eq("id", summary_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="meeting_summary_not_found")
    data = res.data

    # Hydrate the source + derived document names.
    doc_ids = [
        x for x in (data.get("source_document_id"), data.get("derived_document_id"))
        if x
    ]
    if doc_ids:
        docs = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, name")
            .in_("id", doc_ids)
            .execute()
        )
        name_map = {d["id"]: d.get("name") for d in (docs.data or [])}
        data["source_document_name"] = name_map.get(data.get("source_document_id") or "")
        data["derived_document_name"] = name_map.get(data.get("derived_document_id") or "")
    return data


@router.post("/{summary_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess_meeting_summary(
    summary_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Re-fire the MeetingNotesAgent on the underlying transcript.

    Useful when the LLM extraction returned junk, or when the prompt has
    been updated since the original run. Idempotency in the agent itself
    short-circuits on existing summaries, so we delete the row first and
    let the agent recreate it. Admin-only because deletes are destructive.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    org_id: str | None = current_user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization found.")

    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("meeting_summaries")
        .select("id, source_document_id")
        .eq("id", summary_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="meeting_summary_not_found")
    source_doc_id = row.data.get("source_document_id")
    if not source_doc_id:
        raise HTTPException(
            status_code=409,
            detail="Summary has no source document — nothing to reprocess.",
        )

    doc = await asyncio.to_thread(
        lambda: svc.table("documents")
        .select("id, file_type, org_id")
        .eq("id", source_doc_id)
        .maybe_single()
        .execute()
    )
    if not doc or not doc.data or doc.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="source_document_not_found")

    # Drop the summary so the agent's idempotency guard doesn't short-circuit.
    # The derived document is left in place — re-ingesting the transcript
    # would also produce another derived doc, but the unique constraint on
    # source_document_id keeps meeting_summaries deduped after the re-run.
    await asyncio.to_thread(
        lambda: svc.table("meeting_summaries")
        .delete()
        .eq("id", summary_id)
        .eq("org_id", org_id)
        .execute()
    )

    client = get_inngest_client()
    file_type = (doc.data.get("file_type") or "vtt").lower()
    try:
        await client.send(
            inngest.Event(
                name="meeting/transcript-uploaded",
                data={
                    "doc_id": source_doc_id,
                    "org_id": org_id,
                    "file_type": file_type,
                },
                # Append a nonce so this retry doesn't collide with the
                # original Inngest event id.
                id=f"meeting-transcript-{source_doc_id}-reprocess-{summary_id}",
            )
        )
    except Exception as exc:
        log.warning("meeting_reprocess_enqueue_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Couldn't queue the reprocess. Try again.",
        ) from exc

    return {"status": "queued", "source_document_id": source_doc_id}
