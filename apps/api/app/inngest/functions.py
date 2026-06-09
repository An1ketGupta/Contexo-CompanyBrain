"""Inngest function: process an uploaded document end-to-end.

Step layout (status transitions are fine-grained for dashboard visibility; the
heavy ingest work is one step because Inngest caps step output at ~4MB JSON and
file bytes + chunk lists exceed that):

    mark-processing      -> documents.status = 'processing'
    ingest               -> download + parse + chunk + embed + persist
                            (idempotent; embedder has tenacity retries internally)
    mark-ready | mark-failed

If `ingest` raises an unexpected error, Inngest retries the step up to 3 times
with backoff. On final failure, the `on_failure` handler marks the doc failed.
Expected business errors (e.g. EmptyDocumentError, ParseError) are caught
inside `_try_ingest` and reported as a `failed` result without triggering retry.
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
)

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

    log.info("[inngest] process-document doc=%s org=%s path=%s", doc_id, org_id, file_path)

    await step.run("mark-processing", lambda: mark_status(doc_id, "processing"))

    result = await step.run(
        "ingest",
        lambda: _try_ingest(
            doc_id=doc_id,
            org_id=org_id,
            file_path=file_path,
            file_type=file_type,
        ),
    )

    if result["status"] == "ok":
        chunk_count = result["chunk_count"]
        await step.run(
            "mark-ready",
            lambda: mark_status(doc_id, "ready", chunk_count=chunk_count),
        )
        # Notify the uploader. Idempotent via dedupe_key=doc_id so a retried
        # Inngest run never produces a duplicate email.
        await step.run(
            "notify-document-ready",
            lambda: _notify_document_ready(doc_id=doc_id, chunk_count=chunk_count),
        )
    else:
        await step.run(
            "mark-failed",
            lambda: mark_status(doc_id, "failed", error_reason=result["error"]),
        )

    return result


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
        )
        return {"status": "ok", **stats}
    except PipelineError as exc:
        # Known business failure — record it, don't retry.
        return {"status": "failed", "error": str(exc)}


FUNCTIONS: list = [process_document]
