"""Records which chunks actually got cited in an assistant message.

Powers the Day-12 "learning from usage" feedback loop:
    * chunk_citations rows let us see which knowledge surfaced where
    * documents.citation_count bumps drive the retrieval boost in hybrid_search
    * the admin knowledge-intelligence dashboard sums over both

Writes go through the service-role client so the chunk_citations RLS policy
(read-only for org members) doesn't block the insert.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from supabase import Client

from app.database import get_service_client
from app.services.langfuse import observe

log = logging.getLogger(__name__)

_CITATION_BATCH = 100


@observe(name="record_chunk_citations")
async def record_citations(
    *,
    sources: list[dict[str, Any]],
    message_id: str,
    conversation_id: str,
    org_id: str,
    client: Client | None = None,
) -> int:
    """Best-effort: insert one chunk_citations row per source the LLM cited.

    Returns the number of rows actually inserted (0 when there's nothing to
    write — common for pure-conversational turns). Failures are logged and
    swallowed; this is telemetry, not user-facing state.
    """
    if not sources:
        return 0

    rows = list(_build_rows(sources, message_id, conversation_id, org_id))
    if not rows:
        return 0

    svc = client or get_service_client()
    try:
        for start in range(0, len(rows), _CITATION_BATCH):
            batch = rows[start : start + _CITATION_BATCH]
            await asyncio.to_thread(
                lambda b=batch: svc.table("chunk_citations")
                # `upsert` with ignore_duplicates so an Inngest retry of the
                # whole chat turn never trips the unique (chunk_id, message_id)
                # constraint we added in migration 014.
                .upsert(b, on_conflict="chunk_id,message_id", ignore_duplicates=True)
                .execute()
            )
    except Exception as exc:
        log.warning("record_citations insert failed: %s", exc)
        return 0

    # Bump denormalised counts on `documents` via the RPC migration 014 wires
    # up. Atomic SQL update means concurrent assistant turns can't race the
    # counter back down via read-modify-write.
    doc_ids = [r["document_id"] for r in rows if r.get("document_id")]
    if doc_ids:
        try:
            await asyncio.to_thread(
                lambda: svc.rpc(
                    "bump_document_citations",
                    {"doc_ids": doc_ids},
                ).execute()
            )
        except Exception as exc:
            log.warning("bump_document_citations failed: %s", exc)

        # V4 #34: feed the health-score "recency" signal. Best-effort; the
        # nightly cron will use last_accessed_at the next time it runs.
        try:
            from app.services.health_score import touch_last_accessed

            await touch_last_accessed(doc_ids)
        except Exception as exc:
            log.warning("touch_last_accessed failed: %s", exc)

    return len(rows)


def _build_rows(
    sources: Iterable[dict[str, Any]],
    message_id: str,
    conversation_id: str,
    org_id: str,
) -> Iterable[dict[str, Any]]:
    seen: set[str] = set()
    for s in sources:
        chunk_id = s.get("chunk_id")
        document_id = s.get("document_id")
        if not chunk_id or not document_id:
            continue
        if chunk_id in seen:  # defence-in-depth; _dedupe_sources already dedupes
            continue
        seen.add(chunk_id)
        # Use vector_similarity if present (set in _dedupe_sources from the
        # raw cosine); otherwise fall back to the RRF score. Both are useful
        # for later analytics — we just need *something* indicating quality.
        sim_raw = s.get("similarity")
        sim_vec = s.get("vector_similarity")
        sim = sim_vec if sim_vec is not None else sim_raw
        yield {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "message_id": message_id,
            "conversation_id": conversation_id,
            "org_id": org_id,
            "similarity": float(sim) if isinstance(sim, (int, float)) else None,
        }


async def fetch_document_citation_counts(
    *,
    org_id: str,
    document_ids: list[str],
    client: Client | None = None,
) -> dict[str, int]:
    """Read the denormalised counter for a small set of docs.

    Used by the hybrid retrieval boost. Service-role read keeps the latency
    off the chat hot path — the user-scoped client would fire RLS for each
    row even though documents.org_id is already constrained at the API edge.
    """
    if not document_ids:
        return {}
    svc = client or get_service_client()
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("id, citation_count")
            .eq("org_id", org_id)
            .in_("id", document_ids)
            .execute()
        )
    except Exception as exc:
        log.warning("fetch_document_citation_counts failed: %s", exc)
        return {}
    return {
        row["id"]: int(row.get("citation_count") or 0)
        for row in (result.data or [])
    }
