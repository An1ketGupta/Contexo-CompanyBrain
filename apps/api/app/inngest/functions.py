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
    else:
        await step.run(
            "mark-failed",
            lambda: mark_status(doc_id, "failed", error_reason=result["error"]),
        )

    return result


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
