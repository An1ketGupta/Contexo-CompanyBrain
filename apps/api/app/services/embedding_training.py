"""Embedding fine-tune training-pair collection (V5 #106 Phase 1).

Every positive signal (thumbs-up, copy) on an assistant message becomes a
(query, positive_chunk, negative_chunks[]) triple in
`embedding_training_pairs`. We accumulate these silently from day 1 so by the
time an org becomes enterprise-eligible we already have enough data to fine-
tune without a cold-start wait.

Inputs come from `query_logs`:
    - query_text                — the user message that produced the turn
    - retrieved_chunk_ids       — every chunk surfaced by hybrid_search
    - sources (cited chunks)    — derived from messages.sources (the cited
                                  subset of retrieved_chunk_ids)

Negatives = retrieved minus cited. Capped at 5 hard negatives per positive —
more than that bloats the JSONL without measurably improving training.

Idempotency is enforced at the DB layer via a UNIQUE index on
(org_id, positive_chunk_id, md5(query_text), signal_type), so a user spamming
thumbs-up on the same answer can't skew the dataset.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)

SignalType = Literal["copy", "positive_feedback", "high_confidence"]

_MAX_NEGATIVES_PER_POSITIVE = 5


async def collect_training_pairs_for_message(
    *,
    message_id: str,
    org_id: str,
    signal_type: SignalType,
) -> int:
    """Look up the (message, query_log) pair via service-role client and
    insert one training row per cited chunk.

    Returns the number of rows actually inserted (excluding dedupe collisions).
    Fire-and-forget from the caller's perspective — never raises.

    Why we resolve the data here (rather than receiving it from the caller):
    the feedback + copy endpoints don't currently load the source chunks or
    retrieved set; they only know `message_id`. Doing the join here keeps
    those endpoints lightweight and means the training collector is a single
    swap-able service module.
    """
    try:
        return await asyncio.to_thread(_collect_sync, message_id, org_id, signal_type)
    except Exception as exc:
        log.warning(
            "training_pair_collection_failed",
            message_id=message_id,
            org_id=org_id,
            signal=signal_type,
            error=str(exc),
        )
        return 0


def _collect_sync(message_id: str, org_id: str, signal_type: str) -> int:
    svc = get_service_client()

    # 1. Pull the message — `sources` carries the cited chunk ids.
    msg = (
        svc.table("messages")
        .select("id, sources, role, content, org_id")
        .eq("id", message_id)
        .maybe_single()
        .execute()
        .data
    )
    if not msg or msg.get("role") != "assistant" or msg.get("org_id") != org_id:
        return 0

    cited_chunk_ids = _extract_cited_chunk_ids(msg.get("sources") or [])
    if not cited_chunk_ids:
        # No citations means no positives to record — this is the
        # "purely conversational" case (e.g. "hi") and that's fine.
        return 0

    # 2. Pull the matching query_log row — it has query_text + retrieved set.
    qlog = (
        svc.table("query_logs")
        .select("query_text, retrieved_chunk_ids")
        .eq("message_id", message_id)
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not qlog:
        # Could be a turn that predated retrieved_chunk_ids being recorded;
        # not worth backfilling at scale.
        return 0
    row = qlog[0]
    query_text: str = (row.get("query_text") or "").strip()
    if not query_text:
        return 0

    retrieved: list[str] = list(row.get("retrieved_chunk_ids") or [])
    # Negatives = retrieved set minus cited. Cap at 5.
    negatives = [c for c in retrieved if c not in cited_chunk_ids][
        :_MAX_NEGATIVES_PER_POSITIVE
    ]

    # 3. Insert one row per cited chunk. The UNIQUE index swallows dupes.
    inserted = 0
    rows = [
        {
            "org_id": org_id,
            "query_text": query_text[:1000],
            "positive_chunk_id": cid,
            "negative_chunk_ids": negatives,
            "signal_type": signal_type,
        }
        for cid in cited_chunk_ids
    ]
    if not rows:
        return 0

    try:
        # `upsert(..., on_conflict=...)` returns the inserted/conflicted rows;
        # the count of those without `created_at` newer than the call is the
        # safe-enough proxy for "newly inserted". We just trust the row count.
        res = (
            svc.table("embedding_training_pairs")
            .upsert(rows, on_conflict="org_id,positive_chunk_id,signal_type", ignore_duplicates=True)
            .execute()
        )
        inserted = len(res.data or [])
    except Exception as exc:
        # The unique index uses md5(query_text) which isn't expressible as an
        # on_conflict target via PostgREST. Fall back to plain insert + tolerate
        # 23505 (unique violation) per-row.
        log.debug("upsert_fallback_to_insert: %s", exc)
        for r in rows:
            try:
                svc.table("embedding_training_pairs").insert(r).execute()
                inserted += 1
            except Exception as inner:
                # Duplicate key = expected; anything else we want to know about.
                if "duplicate key" not in str(inner).lower():
                    log.warning(
                        "training_pair_insert_failed",
                        org_id=org_id,
                        error=str(inner),
                    )

    return inserted


def _extract_cited_chunk_ids(sources: list[dict] | list[str]) -> list[str]:
    """Sources is a JSONB array stored on `messages.sources`. Shape historically:
    [{ chunk_id, document_id, ... }, ...]. We pull chunk_id and dedupe."""
    out: list[str] = []
    seen: set[str] = set()
    for src in sources or []:
        if isinstance(src, str):
            cid = src
        elif isinstance(src, dict):
            cid = src.get("chunk_id") or ""
        else:
            cid = ""
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


__all__ = ["collect_training_pairs_for_message"]
