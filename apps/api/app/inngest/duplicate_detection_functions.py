"""Inngest functions for duplicate detection (Agent2 Day 2 #14).

Two functions:

  * ``document_duplicate_scan`` — handles ``document/duplicate.scan``, fired
    from services/document_summary.py once the summary embedding is in place.
    Wraps services/duplicate_detection.scan_document_for_duplicates.

  * ``backfill_summary_embeddings`` — one-shot maintenance function.
    Iterates docs with status='ready' and summary text but no
    summary_embedding, embeds them in batches of 50, queues a scan for each.
    Fired manually for the migration backfill or via the admin endpoint
    when a customer wants to re-baseline.

Why a maintenance function vs. one big query:
    The backfill touches every legacy doc in the org. Doing it in the API
    process would either block the worker on a long sync run OR require
    chunked pagination + state. Inngest gives us pagination + retries + a
    stop button for free.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.duplicate_detection import scan_document_for_duplicates

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


@_inngest_client.create_function(
    fn_id="document-duplicate-scan",
    trigger=inngest.TriggerEvent(event="document/duplicate.scan"),
    retries=2,
    # Per-doc concurrency=1 so a doc that re-uploads (new version → new
    # summary embedding) doesn't run two scans in parallel.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.document_id")],
)
async def document_duplicate_scan(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data or {}
    document_id = data.get("document_id")
    org_id = data.get("org_id")
    if not document_id or not org_id:
        return {"status": "skipped", "reason": "missing_fields"}

    result = await ctx.step.run(
        "scan",
        lambda: scan_document_for_duplicates(
            document_id=document_id, org_id=org_id
        ),
    )
    return result


# ── Backfill ─────────────────────────────────────────────────────────────


_BATCH_SIZE = 50


@_inngest_client.create_function(
    fn_id="backfill-summary-embeddings",
    trigger=inngest.TriggerEvent(event="document/backfill.summary-embeddings"),
    retries=3,
    # Only one backfill at a time across all orgs. The job is cheap per-row
    # but high-throughput; running two concurrent backfills would just
    # hammer Gemini's embeddings API.
    concurrency=[inngest.Concurrency(limit=1)],
)
async def backfill_summary_embeddings(ctx: inngest.Context) -> dict[str, Any]:
    """Embed summaries for legacy docs that don't have ``summary_embedding`` yet.

    Strategy: page through ready docs with a non-empty summary text but
    NULL summary_embedding, in ``_BATCH_SIZE`` batches. For each, run the
    same code path as a fresh summary by calling generate_document_summary
    (which is idempotent on the summary text but always re-embeds if the
    embedding is missing).
    """
    total_processed = 0
    total_embedded = 0

    # Page-by-page so a Postgres CONNECTION TIMEOUT mid-backfill doesn't
    # restart from doc 0. Each `step.run` is independently retried.
    page = 0
    while True:
        batch = await ctx.step.run(
            f"page-{page}",
            lambda p=page: _fetch_unembedded_batch(offset=p * _BATCH_SIZE),
        )
        if not batch:
            break

        for row in batch:
            total_processed += 1
            try:
                from app.services.document_summary import generate_document_summary

                # If summary already exists, generate_document_summary's
                # "skipped" guard will short-circuit BEFORE we get a chance
                # to embed. So we directly embed here when the summary is
                # already present.
                result = await ctx.step.run(
                    f"embed-{row['id']}",
                    lambda r=row: _embed_existing_summary(r),
                )
                if result.get("embedded"):
                    total_embedded += 1
            except Exception as exc:
                log.warning(
                    "backfill_summary_embedding_row_failed doc=%s err=%s",
                    row["id"],
                    exc,
                )

        # Stop if the page was smaller than batch size (no more rows).
        if len(batch) < _BATCH_SIZE:
            break
        page += 1

    log.info(
        "backfill.summary_embeddings.done",
        processed=total_processed,
        embedded=total_embedded,
    )
    return {"processed": total_processed, "embedded": total_embedded}


def _fetch_unembedded_batch(*, offset: int) -> list[dict[str, Any]]:
    """Ready docs that have a summary but no summary_embedding."""
    svc = get_service_client()
    # PostgREST: `metadata->>summary is not null` for JSONB scalar lookup.
    res = (
        svc.table("documents")
        .select("id, org_id, name, metadata")
        .eq("status", "ready")
        .is_("summary_embedding", "null")
        .not_.is_("metadata->>summary", "null")
        .order("created_at", desc=False)
        .range(offset, offset + _BATCH_SIZE - 1)
        .execute()
    )
    return res.data or []


async def _embed_existing_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Embed the (already-present) summary text and persist on the row.

    Synchronous: the caller is an Inngest step.run, so blocking is fine
    inside the to_thread wrappers.
    """
    document_id = row["id"]
    org_id = row["org_id"]
    metadata = row.get("metadata") or {}
    summary = metadata.get("summary") or ""
    topics = metadata.get("key_topics") or []
    name = row.get("name") or ""

    text = " ".join([name, summary, " ".join(topics)]).strip()
    if not text:
        return {"embedded": False, "reason": "no_summary_text"}

    from app.services.ingestion.embedder import get_embedder

    embedder = get_embedder()
    vecs = await embedder.embed_texts([text], task_type="RETRIEVAL_DOCUMENT")
    if not vecs or not vecs[0]:
        return {"embedded": False, "reason": "embedding_empty"}
    vector = list(vecs[0])

    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("documents")
        .update(
            {
                "summary_embedding": vector,
                "summary_embedded_at": datetime.now(UTC).isoformat(),
            }
        )
        .eq("id", document_id)
        .execute()
    )

    # Queue a scan for this doc now that it has an embedding.
    try:
        await get_inngest_client().send(
            inngest.Event(
                name="document/duplicate.scan",
                data={"document_id": document_id, "org_id": org_id},
            )
        )
    except Exception as exc:
        log.warning("backfill_scan_enqueue_failed doc=%s err=%s", document_id, exc)

    return {"embedded": True}


FUNCTIONS = [document_duplicate_scan, backfill_summary_embeddings]
