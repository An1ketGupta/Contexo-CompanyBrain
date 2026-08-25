"""Document-level duplicate detection (Agent2 Day 2 #14).

Triggered as a post-ingest step: when ``document_summary.generate_document_summary``
finishes successfully it also writes ``documents.summary_embedding`` and
queues ``document/duplicate.scan`` for that doc. The Inngest worker calls
:func:`scan_document_for_duplicates` here.

Why doc-level summary embeddings and not chunk averages:
    Two HR policies that share onboarding boilerplate can score 0.9 at the
    chunk level — useful for retrieval, useless for "are these the same doc."
    The summary embedding compresses each doc to one vector that captures
    its *topic* rather than its surface text, giving cleaner duplicate
    signals.

What we do when a match is found:
    * Persist a notification for org admins ("'Q3 hiring plan' may already
      exist as 'Hiring plan — Q3'").
    * The actual decision (merge / replace / keep both) is admin UX, not
      part of this module.

Thresholds:
    * Notification trigger: ≥ 0.85 cosine similarity (set in
      DEFAULT_NOTIFY_THRESHOLD).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client
from app.services.langfuse import observe

log = logging.getLogger(__name__)

DEFAULT_NOTIFY_THRESHOLD = 0.85
MAX_MATCHES_PER_SCAN = 5


@observe(name="duplicate_detection.scan_document_for_duplicates")
async def scan_document_for_duplicates(
    *,
    document_id: str,
    org_id: str,
    threshold: float = DEFAULT_NOTIFY_THRESHOLD,
) -> dict[str, Any]:
    """Find similar documents to `document_id`, notify admins on a match.

    Idempotent against admin notifications: the dedupe_key is
    ``duplicate:{document_id}:{top_match_id}`` so re-running the scan
    doesn't re-notify if the same pair is still the closest match.
    """
    matches = await find_similar_documents(
        org_id=org_id, document_id=document_id, threshold=threshold
    )
    if not matches:
        return {"status": "no_match", "document_id": document_id}

    top = matches[0]
    # Fetch the source doc's name for a friendly notification body.
    name = await _fetch_document_name(document_id) or "A document"

    admin_ids = await _fetch_admin_ids(org_id)
    delivered = 0
    if admin_ids:
        from app.services.notifications import create_notification

        for admin_id in admin_ids:
            row = await create_notification(
                org_id=org_id,
                user_id=admin_id,
                type="duplicate_document_detected",
                title="Possible duplicate document detected",
                body=(
                    f"'{name}' looks ~{int(top['similarity'] * 100)}% similar to "
                    f"'{top['doc_name']}'. Review and decide whether to merge or keep both."
                ),
                metadata={
                    "document_id": document_id,
                    "match_document_id": top["doc_id"],
                    "similarity": top["similarity"],
                    "matches": matches,
                },
                link_url=f"/admin/duplicates?doc={document_id}",
                dedupe_key=f"duplicate:{document_id}:{top['doc_id']}",
            )
            if row:
                delivered += 1

    return {
        "status": "matched",
        "document_id": document_id,
        "matches": matches,
        "notifications_sent": delivered,
    }


# ── DB helpers ──────────────────────────────────────────────────────────


async def find_similar_documents(
    *,
    org_id: str,
    document_id: str,
    threshold: float = DEFAULT_NOTIFY_THRESHOLD,
    limit: int = MAX_MATCHES_PER_SCAN,
) -> list[dict[str, Any]]:
    """Wrap the find_similar_documents Postgres RPC.

    Returns an ordered list of {doc_id, doc_name, similarity} dicts, top
    match first. Empty list if the source doc has no summary embedding yet.
    """
    svc = get_service_client()

    def _run() -> list[dict[str, Any]]:
        res = svc.rpc(
            "find_similar_documents",
            {
                "p_org_id": org_id,
                "p_document_id": document_id,
                "p_threshold": threshold,
                "p_limit": limit,
            },
        ).execute()
        return res.data or []

    try:
        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        log.warning("find_similar_documents_failed doc=%s err=%s", document_id, exc)
        return []
    return rows


async def _fetch_document_name(document_id: str) -> str | None:
    svc = get_service_client()

    def _run() -> str | None:
        res = (
            svc.table("documents")
            .select("name")
            .eq("id", document_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("name") if res else None

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return None


async def _fetch_admin_ids(org_id: str) -> list[str]:
    svc = get_service_client()

    def _run() -> list[str]:
        res = (
            svc.table("users")
            .select("id")
            .eq("org_id", org_id)
            .eq("role", "admin")
            .execute()
        )
        return [r["id"] for r in (res.data or [])]

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return []
