"""Inngest functions: ingest a fresh document, or retry only its failed chunks.

`process-document` (doc/uploaded): full pipeline.
`retry-failed-chunks` (doc/retry-chunks): targeted re-embed of `failed` chunks.

Outcome mapping for `process-document`:
    embedded > 0, failed == 0   → status = ready
    embedded > 0, failed > 0    → status = ready, metadata.embedding = {…}
                                  (the UI surfaces the partial banner from this)
    embedded == 0               → status = failed
"""
from __future__ import annotations

import logging
from typing import Any

import inngest

from app.services.ingestion import (
    PipelineError,
    download_from_storage,
    mark_status,
    process_document as run_pipeline,
    reembed_failed_chunks,
)
from app.database import get_service_client
from app.services.health_score import recompute_org_health
from app.services.summarization import summarize_conversation
from app.services.webhooks import trigger_event as trigger_webhook_event

from .client import get_inngest_client

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


async def _on_failure(ctx: inngest.Context) -> None:
    """Fires after Inngest exhausts retries on the main function."""
    step = ctx.step
    data = ctx.event.data
    doc_id = data.get("doc_id")
    error = str(getattr(ctx, "error", None) or "ingestion exhausted retries")
    if doc_id:
        await step.run(
            "mark-failed-after-retries",
            lambda: mark_status(doc_id, "failed", error_reason=error),
        )


@_inngest_client.create_function(
    fn_id="process-document",
    trigger=inngest.TriggerEvent(event="doc/uploaded"),
    retries=3,
    concurrency=[
        inngest.Concurrency(
            limit=3,
            key="event.data.org_id",
            scope="fn",
        ),
    ],
    on_failure=_on_failure,
)
async def process_document(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    doc_id: str = data["doc_id"]
    org_id: str = data["org_id"]
    file_path: str = data["file_path"]
    file_type: str = data["file_type"]
    # V4 #68 — present when this run is processing a new document version.
    # NULL for first-time uploads (no version row exists for legacy docs).
    version_id: str | None = data.get("version_id")

    log.info("[inngest] process-document doc=%s org=%s path=%s version=%s", doc_id, org_id, file_path, version_id)

    await step.run("mark-processing", lambda: mark_status(doc_id, "processing"))

    result = await step.run(
        "ingest",
        lambda: _try_ingest(
            doc_id=doc_id,
            org_id=org_id,
            file_path=file_path,
            file_type=file_type,
            version_id=version_id,
        ),
    )

    if result["status"] == "ok":
        embedded = int(result["embedded"])
        failed = int(result["failed"])
        total = int(result["chunk_count"])
        # Even a single failed chunk is worth surfacing — fold the per-chunk
        # tally into metadata.embedding so the documents UI can render the
        # partial badge without a second round-trip.
        if embedded == 0:
            await step.run(
                "mark-failed",
                lambda: mark_status(
                    doc_id,
                    "failed",
                    error_reason="All chunks failed to embed. Check upstream LLM provider.",
                    embedding_stats={"embedded": 0, "failed": failed, "total": total},
                ),
            )
            await step.run(
                "webhook-document-failed",
                lambda: _fire_doc_webhook(
                    org_id=org_id, doc_id=doc_id, event="document.failed",
                    error="All chunks failed to embed.",
                ),
            )
        else:
            stats = {"embedded": embedded, "failed": failed, "total": total} if failed else None
            await step.run(
                "mark-ready",
                lambda s=stats: mark_status(
                    doc_id, "ready", chunk_count=total, embedding_stats=s,
                ),
            )
            # Newly-ready doc may shift coverage. Drop the cache eagerly so the
            # admin Coverage page reflects this upload without waiting on TTL.
            await step.run(
                "invalidate-coverage-cache",
                lambda: _invalidate_coverage(org_id=org_id),
            )
            # Disabled: using browser notifications instead (placeholder for later)
            # await step.run(
            #     "notify-document-ready",
            #     lambda: _notify_document_ready(doc_id=doc_id, chunk_count=embedded),
            # )
            await step.run(
                "webhook-document-processed",
                lambda e=embedded, t=total: _fire_doc_webhook(
                    org_id=org_id, doc_id=doc_id, event="document.processed",
                    chunks_embedded=e, chunks_total=t,
                ),
            )
    else:
        await step.run(
            "mark-failed",
            lambda: mark_status(doc_id, "failed", error_reason=result["error"]),
        )
        await step.run(
            "webhook-document-failed",
            lambda r=result: _fire_doc_webhook(
                org_id=org_id, doc_id=doc_id, event="document.failed",
                error=r.get("error", "ingestion failed"),
            ),
        )

    return result


@_inngest_client.create_function(
    fn_id="retry-failed-chunks",
    trigger=inngest.TriggerEvent(event="doc/retry-chunks"),
    retries=2,
    concurrency=[
        inngest.Concurrency(
            limit=3,
            key="event.data.org_id",
            scope="fn",
        ),
    ],
)
async def retry_failed_chunks(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step
    data = ctx.event.data
    doc_id: str = data["doc_id"]
    org_id: str = data["org_id"]

    log.info("[inngest] retry-failed-chunks doc=%s", doc_id)

    result = await step.run(
        "retry",
        lambda: _try_retry(doc_id=doc_id, org_id=org_id),
    )

    if result["status"] == "ok":
        # Fetch the current chunks tally to compute the new partial state.
        stats = await step.run(
            "refresh-status",
            lambda: _refresh_after_retry(doc_id=doc_id, org_id=org_id),
        )
        return {"status": "ok", **stats}

    return result


async def _refresh_after_retry(*, doc_id: str, org_id: str) -> dict[str, Any]:
    """Recompute embedded/failed/total from chunks table and update status."""
    from app.database import get_service_client
    import asyncio as _asyncio

    svc = get_service_client()
    rows = await _asyncio.to_thread(
        lambda: svc.table("chunks")
        .select("embedding_status")
        .eq("document_id", doc_id)
        .execute()
    )
    statuses = [r["embedding_status"] for r in rows.data or []]
    total = len(statuses)
    embedded = sum(1 for s in statuses if s == "embedded")
    failed = sum(1 for s in statuses if s == "failed")

    if total == 0:
        return {"total": 0, "embedded": 0, "failed": 0}

    if embedded == 0:
        await mark_status(
            doc_id,
            "failed",
            error_reason="All chunks failed to embed after retry.",
            embedding_stats={"embedded": 0, "failed": failed, "total": total},
        )
    else:
        stats = {"embedded": embedded, "failed": failed, "total": total} if failed else None
        await mark_status(doc_id, "ready", chunk_count=total, embedding_stats=stats)
        await _invalidate_coverage(org_id=org_id)

    return {"total": total, "embedded": embedded, "failed": failed}


async def _invalidate_coverage(*, org_id: str) -> dict[str, str]:
    """Drop the cached coverage_scores row. Wrapper for Inngest step.run.

    Imported lazily to keep the inngest module's cold-start light and to
    avoid a circular import with services.coverage which itself only pulls
    from database + embedder.
    """
    from app.services.coverage import invalidate_coverage

    await invalidate_coverage(org_id)
    return {"status": "invalidated"}


async def _try_retry(*, doc_id: str, org_id: str) -> dict[str, Any]:
    try:
        stats = await reembed_failed_chunks(doc_id=doc_id, org_id=org_id)
        return {
            "status": "ok",
            "chunk_count": stats.chunk_count,
            "embedded": stats.embedded,
            "failed": stats.failed,
        }
    except PipelineError as exc:
        return {"status": "failed", "error": str(exc)}


async def _fire_doc_webhook(
    *,
    org_id: str,
    doc_id: str,
    event: str,
    chunks_embedded: int | None = None,
    chunks_total: int | None = None,
    error: str | None = None,
) -> None:
    """Best-effort document lifecycle webhook fan-out.

    We resolve the doc name here so receivers don't have to make a follow-up
    API call to get a human-readable identifier in their own dashboards.
    """
    from app.database import get_service_client
    import asyncio as _asyncio

    svc = get_service_client()
    name: str | None = None
    file_type: str | None = None
    try:
        doc = await _asyncio.to_thread(
            lambda: svc.table("documents")
            .select("name, file_type")
            .eq("id", doc_id)
            .maybe_single()
            .execute()
        )
        if doc and doc.data:
            name = doc.data.get("name")
            file_type = doc.data.get("file_type")
    except Exception:
        pass

    payload: dict[str, Any] = {
        "document_id": doc_id,
        "name": name,
        "file_type": file_type,
    }
    if chunks_embedded is not None:
        payload["chunks_embedded"] = chunks_embedded
    if chunks_total is not None:
        payload["chunks_total"] = chunks_total
    if error:
        payload["error"] = error

    try:
        await trigger_webhook_event(org_id=org_id, event=event, payload=payload)
    except Exception as exc:
        log.warning("[inngest] webhook fire failed: event=%s err=%s", event, exc)


async def _notify_document_ready(*, doc_id: str, chunk_count: int) -> None:
    """Best-effort email to the uploader. Failures here don't fail the run."""
    from app.config import get_settings
    from app.database import get_service_client
    from app.services.email import send_email_event
    import asyncio as _asyncio

    settings = get_settings()
    svc = get_service_client()

    try:
        doc = await _asyncio.to_thread(
            lambda: svc.table("documents")
            .select("name, org_id, created_by")
            .eq("id", doc_id)
            .maybe_single()
            .execute()
        )
        if not doc or not doc.data or not doc.data.get("created_by"):
            return
        uploader_id = doc.data["created_by"]

        au = await _asyncio.to_thread(
            lambda: svc.auth.admin.get_user_by_id(uploader_id)
        )
        email = getattr(getattr(au, "user", None), "email", None)
        if not email:
            return

        await send_email_event(
            event_type="document_ready",
            to=email,
            user_id=uploader_id,
            org_id=doc.data["org_id"],
            dedupe_key=doc_id,
            data={
                "doc_name": doc.data["name"],
                "chunk_count": chunk_count,
                "app_url": settings.app_url,
            },
        )
    except Exception as exc:
        log.warning("[inngest] document_ready notify failed: %s", exc)


async def _try_ingest(
    *,
    doc_id: str,
    org_id: str,
    file_path: str,
    file_type: str,
    version_id: str | None = None,
) -> dict[str, Any]:
    """Run the pipeline. Business failures → {'status': 'failed', 'error'}.
    Unexpected failures bubble up so Inngest can retry.
    """
    try:
        file_bytes = await download_from_storage(file_path)
        stats = await run_pipeline(
            doc_id=doc_id,
            org_id=org_id,
            file_bytes=file_bytes,
            file_type=file_type,
            document_version_id=version_id,
        )
        return {
            "status": "ok",
            "chunk_count": stats.chunk_count,
            "embedded": stats.embedded,
            "failed": stats.failed,
            "total_tokens": stats.total_tokens,
        }
    except PipelineError as exc:
        # Known business failure — record it, don't retry.
        return {"status": "failed", "error": str(exc)}


@_inngest_client.create_function(
    fn_id="summarize-conversation",
    trigger=inngest.TriggerEvent(event="conversation/turn-saved"),
    retries=1,
    # Bound concurrency per org so a runaway tab doesn't fan us out.
    concurrency=[
        inngest.Concurrency(limit=2, key="event.data.org_id", scope="fn"),
    ],
)
async def summarize_conversation_fn(ctx: inngest.Context) -> dict[str, Any]:
    """Best-effort: tail of every assistant turn. summarize_conversation()
    is idempotent and short-circuits when no refresh is needed, so the cost
    of a no-op invocation is one PostgREST round trip."""
    data = ctx.event.data
    conversation_id: str = data["conversation_id"]
    result = await ctx.step.run(
        "summarize",
        lambda: summarize_conversation(conversation_id=conversation_id),
    )
    return result


# ── V4 #34 — Nightly knowledge health recompute ─────────────────────────────
# Runs at 02:00 UTC every day. Loops over orgs and recomputes health_score for
# every ready document. Per-org step.run isolation means one bad org doesn't
# block the rest, and we can re-run a single fan-out child without re-doing
# the whole batch.
@_inngest_client.create_function(
    fn_id="recompute-document-health",
    trigger=inngest.TriggerCron(cron="0 2 * * *"),
    retries=1,
)
async def recompute_document_health_fn(ctx: inngest.Context) -> dict[str, Any]:
    step = ctx.step

    async def _fetch_active_org_ids() -> list[str]:
        svc = get_service_client()
        # Only recompute for orgs with at least one ready document. New orgs
        # that haven't uploaded anything don't need a row, and listing them
        # would just inflate the fan-out step count.
        res = svc.table("documents") \
            .select("org_id") \
            .eq("status", "ready") \
            .limit(50000) \
            .execute()
        return sorted({row["org_id"] for row in (res.data or []) if row.get("org_id")})

    org_ids = await step.run("list-orgs", _fetch_active_org_ids)
    total_updated = 0
    for org_id in org_ids:
        updated = await step.run(
            f"recompute-{org_id}",
            lambda oid=org_id: recompute_org_health(oid),
        )
        total_updated += int(updated or 0)
    return {"orgs": len(org_ids), "documents_updated": total_updated}


FUNCTIONS: list = [
    process_document,
    retry_failed_chunks,
    summarize_conversation_fn,
    recompute_document_health_fn,
]
