"""Collection → document_ids resolver for scoped retrieval.

Collections are tag-derived (migration 025): a document is "in" a collection
if its `tags` array overlaps the collection's `tag_filters`. Retrieval is
document-centric, so to scope a search to a collection we first resolve the
collection to its document_id set, then pass that list into hybrid_search's
existing `document_ids` filter.

Why a separate helper (not a flag on hybrid_search):
  * Keeps hybrid_search provider-agnostic — no schema knowledge of collections.
  * Lets us cache the resolution per (org_id, collection_id) in a future pass
    without touching the retrieval module.
  * Makes "scope to org's default rfp collection" a one-liner in agent code.

Returns an explicit list (possibly empty). An empty list semantically means
"this collection contains zero documents" — the caller MUST treat that as a
no-op rather than fall through to "no filter" (which would be a privacy bug).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from supabase import Client

from app.database import get_service_client

log = logging.getLogger(__name__)


async def resolve_collection_document_ids(
    *,
    org_id: str,
    collection_id: str,
    client: Client | None = None,
    limit: int = 10_000,
) -> list[str]:
    """Resolve a collection to the document_ids it currently contains.

    Empty tag_filters → empty list (a collection with no filters matches
    nothing by convention; an admin who wants the whole KB should pass
    collection_id=None instead of pointing at an empty collection).
    """
    svc = client or get_service_client()

    def _fetch_collection() -> dict[str, Any] | None:
        res = (
            svc.table("collections")
            .select("tag_filters")
            .eq("id", collection_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    coll = await asyncio.to_thread(_fetch_collection)
    if not coll:
        log.warning(
            "rfp.collection_scope: collection_not_found org=%s coll=%s",
            org_id,
            collection_id,
        )
        return []

    tag_filters: list[str] = [t for t in (coll.get("tag_filters") or []) if t]
    if not tag_filters:
        return []

    def _fetch_docs() -> list[dict[str, Any]]:
        res = (
            svc.table("documents")
            .select("id")
            .eq("org_id", org_id)
            .overlaps("tags", tag_filters)
            .limit(limit)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch_docs)
    return [r["id"] for r in rows if r.get("id")]


async def resolve_org_rfp_scope(
    *,
    org_id: str,
    override_collection_id: str | None,
    client: Client | None = None,
) -> tuple[list[str] | None, str | None]:
    """Resolve the document scope for an RFP retrieval.

    Precedence:
      1. override_collection_id (per-RFP override on rfp_responses.collection_id)
      2. organizations.rfp_approved_collection_id (org default, migration 077)
      3. None — fall through to the whole KB

    Returns (document_ids_or_None, resolved_collection_id_or_None). The first
    element is a list when a collection is in play (possibly empty list — must
    be respected!), or None when no collection is configured.
    """
    svc = client or get_service_client()

    collection_id = override_collection_id
    if not collection_id:
        def _fetch_org() -> dict[str, Any] | None:
            res = (
                svc.table("organizations")
                .select("rfp_approved_collection_id")
                .eq("id", org_id)
                .maybe_single()
                .execute()
            )
            return res.data if res else None

        row = await asyncio.to_thread(_fetch_org)
        collection_id = (row or {}).get("rfp_approved_collection_id")

    if not collection_id:
        return None, None

    doc_ids = await resolve_collection_document_ids(
        org_id=org_id, collection_id=collection_id, client=svc
    )
    return doc_ids, collection_id
