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

import asyncio
import logging
from datetime import UTC
from typing import Any

import inngest

from app.database import get_service_client
from app.services.document_summary import generate_document_summary
from app.services.health_score import recompute_org_health
from app.services.ingestion import (
    PipelineError,
    download_from_storage,
    mark_status,
    reembed_failed_chunks,
)
from app.services.ingestion import (
    process_document as run_pipeline,
)
from app.services.summarization import summarize_conversation
from app.services.toc_extractor import extract_toc
from app.services.toc_extractor import to_json as toc_to_json

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
            # V3 #80 — the doc list cache held the 'processing' row; clear it
            # so the next refresh shows 'failed' immediately.
            await step.run(
                "invalidate-doc-list-cache-on-failed",
                lambda: _invalidate_doc_list(org_id=org_id),
            )
        else:
            stats = {"embedded": embedded, "failed": failed, "total": total} if failed else None
            await step.run(
                "mark-ready",
                lambda s=stats: mark_status(
                    doc_id, "ready", chunk_count=total, embedding_stats=s,
                ),
            )
            # V3 #80 — the doc list & search caches still show 'processing';
            # bump the version so the next list call (and any in-flight chat
            # search) sees the new ready state without waiting on TTL.
            await step.run(
                "invalidate-doc-list-cache-on-ready",
                lambda: _invalidate_doc_list(org_id=org_id),
            )
            # Disabled: using browser notifications instead (placeholder for later)
            # await step.run(
            #     "notify-document-ready",
            #     lambda: _notify_document_ready(doc_id=doc_id, chunk_count=embedded),
            # )
            # V5 #107 — structural TOC. Zero LLM cost, runs after `ready` so
            # users can already chat while it computes. Best-effort: errors are
            # swallowed inside _extract_doc_toc — never fail the function.
            await step.run(
                "extract-toc",
                lambda fp=file_path, ft=file_type: _extract_doc_toc(
                    doc_id=doc_id, file_path=fp, file_type=ft,
                ),
            )
            # V5 #24 — auto-summary + key topics. One LLM call, capped input.
            # Fires last so the doc is queryable immediately and the summary
            # chips simply appear a few seconds later via Supabase Realtime.
            await step.run(
                "generate-summary",
                lambda: _generate_doc_summary(doc_id=doc_id),
            )
            # Agent Day 12 — auto-tagging from a fixed taxonomy. Runs after
            # mark-ready so the document is queryable while tag chips
            # populate via Realtime. Skipped if the user manually tagged
            # already (handled inside auto_tag_document). Best-effort.
            await step.run(
                "auto-tag-document",
                lambda: _auto_tag_doc(doc_id=doc_id),
            )
            # Agent2 Day 2 #33 — once tags are in place, run smart routing
            # to propose a collection assignment. Best-effort: no centroids
            # yet (empty org) or no match → silent no-op.
            await step.run(
                "smart-route-document",
                lambda: _smart_route_doc(doc_id=doc_id, org_id=org_id),
            )
            # Agent Day 13 — meeting-transcript routing. If the file
            # extension says it's a meeting transcript we fan out a
            # dedicated event for the MeetingNotesAgent. Cheap inline
            # check; non-transcript docs are a no-op.
            await step.run(
                "maybe-route-meeting-transcript",
                lambda ft=file_type: _maybe_route_meeting_transcript(
                    doc_id=doc_id, org_id=org_id, file_type=ft,
                ),
            )
            # Agent Day 9 — fan out a policy-propagation event if this doc
            # carries the `policy` tag OR is marked requires_acknowledgement.
            # We do the cheap eligibility check inline so non-policy docs
            # (the common case) never enqueue a no-op agent run.
            await step.run(
                "maybe-propagate-policy",
                lambda v=version_id: _maybe_trigger_policy_propagation(
                    doc_id=doc_id, org_id=org_id, version_id=v,
                ),
            )
            # Agent Day 15 — generic "what changed" diff for non-policy
            # versioned uploads. Skipped automatically for first uploads
            # and for policy-tagged docs (those are handled above by the
            # policy propagation agent which already writes document_diffs).
            await step.run(
                "maybe-run-version-diff",
                lambda v=version_id: _maybe_trigger_version_diff(
                    doc_id=doc_id, org_id=org_id, version_id=v,
                ),
            )
    else:
        await step.run(
            "mark-failed",
            lambda: mark_status(doc_id, "failed", error_reason=result["error"]),
        )
        await step.run(
            "invalidate-doc-list-cache-on-pipeline-failed",
            lambda: _invalidate_doc_list(org_id=org_id),
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
    import asyncio as _asyncio

    from app.database import get_service_client

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
        await _invalidate_doc_list(org_id=org_id)
    else:
        stats = {"embedded": embedded, "failed": failed, "total": total} if failed else None
        await mark_status(doc_id, "ready", chunk_count=total, embedding_stats=stats)
        await _invalidate_doc_list(org_id=org_id)

    return {"total": total, "embedded": embedded, "failed": failed}


async def _invalidate_doc_list(*, org_id: str) -> dict[str, str]:
    """V3 #80 — bump the docs-list/search cache version for this org.

    Called from Inngest steps after a document's status changes to a value
    the UI cares about (ready/failed). Wrapper exists so step.run gets a
    named function for retries + telemetry.
    """
    from app.services.documents_cache import invalidate_document_caches

    await invalidate_document_caches(org_id)
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


async def _notify_document_ready(*, doc_id: str, chunk_count: int) -> None:
    """Best-effort email to the uploader. Failures here don't fail the run."""
    import asyncio as _asyncio

    from app.config import get_settings
    from app.database import get_service_client
    from app.services.email import send_email_event

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


async def _extract_doc_toc(*, doc_id: str, file_path: str, file_type: str) -> dict[str, Any]:
    """Re-download the file, run structural TOC extraction, persist into metadata.

    Best-effort: a parse failure or storage hiccup logs a warning and returns
    a no-op status — the document is already 'ready' and queryable. Never raises
    so Inngest doesn't retry the whole ingest run on a TOC-only failure.
    """
    try:
        if file_type.lower() not in ("pdf", "docx"):
            return {"status": "skipped", "reason": "unsupported_type"}
        file_bytes = await download_from_storage(file_path)
        entries = await asyncio.to_thread(lambda: extract_toc(file_bytes, file_type))
        if not entries:
            return {"status": "skipped", "reason": "no_headings"}

        svc = get_service_client()
        # Re-read metadata to merge so we never clobber summary/embedding state
        # that another concurrent step may have just written.
        doc = await asyncio.to_thread(
            lambda: svc.table("documents")
            .select("metadata")
            .eq("id", doc_id)
            .maybe_single()
            .execute()
        )
        existing = (doc.data.get("metadata") if doc and doc.data else None) or {}
        if not isinstance(existing, dict):
            existing = {}
        next_metadata = {
            **existing,
            "toc": toc_to_json(entries),
            "toc_generated_at": _utcnow_iso(),
        }
        await asyncio.to_thread(
            lambda: svc.table("documents")
            .update({"metadata": next_metadata})
            .eq("id", doc_id)
            .execute()
        )
        return {"status": "ok", "entries": len(entries)}
    except Exception as exc:
        log.warning("[inngest] TOC extraction failed: doc=%s err=%s", doc_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}


async def _maybe_trigger_version_diff(
    *, doc_id: str, org_id: str, version_id: str | None,
) -> dict[str, Any]:
    """Fire `doc/version-diff` when this is a non-first, non-policy version.

    The gate inside `should_run_diff` makes this safe to call for every doc;
    we only enqueue when there's actually work to do.
    """
    try:
        from app.services.agents.version_diff_agent import should_run_diff

        if not await should_run_diff(document_id=doc_id, org_id=org_id):
            return {"status": "skipped", "reason": "not_eligible"}

        client = get_inngest_client()
        event_id = f"version-diff-{doc_id}-{version_id or 'current'}"
        await client.send(
            inngest.Event(
                name="doc/version-diff",
                data={
                    "document_id": doc_id,
                    "org_id": org_id,
                    "version_id": version_id,
                },
                id=event_id,
            )
        )
        return {"status": "fired", "event_id": event_id}
    except Exception as exc:
        log.warning("[inngest] version diff trigger failed: %s", exc)
        return {"status": "failed", "reason": str(exc)[:200]}


async def _maybe_trigger_policy_propagation(
    *, doc_id: str, org_id: str, version_id: str | None,
) -> dict[str, Any]:
    """Fire `agent/policy-propagate` for policy-tagged or ack-required docs.

    Why inline this gate (rather than always fire and let the agent skip):
    every fired event costs us an Inngest run + audit row. The vast majority
    of doc uploads are non-policy; gating here keeps the audit log clean and
    saves an LLM round-trip on the skip path.
    """
    try:
        from app.services.agents.policy_propagation_agent import should_propagate

        if not await should_propagate(document_id=doc_id, org_id=org_id):
            return {"status": "skipped", "reason": "not_policy"}

        client = get_inngest_client()
        # Idempotency: one event per (doc, version). Re-fires of the same
        # version (Inngest retry) collapse into one agent run.
        event_id = f"policy-propagate-{doc_id}-{version_id or 'current'}"
        await client.send(
            inngest.Event(
                name="agent/policy-propagate",
                data={
                    "document_id": doc_id,
                    "org_id": org_id,
                    "version_id": version_id,
                },
                id=event_id,
            )
        )
        return {"status": "fired", "event_id": event_id}
    except Exception as exc:
        log.warning("[inngest] policy propagation trigger failed: %s", exc)
        return {"status": "failed", "reason": str(exc)[:200]}


async def _generate_doc_summary(*, doc_id: str) -> dict[str, Any]:
    """Wrapper around services.document_summary so Inngest gets a serializable dict.

    `generate_document_summary` already swallows LLM errors and returns its own
    status dict, but we add one more try/except for paranoia — a single bad
    document should never cascade into an Inngest retry storm.
    """
    try:
        return await generate_document_summary(document_id=doc_id)
    except Exception as exc:
        log.warning("[inngest] summary generation failed: doc=%s err=%s", doc_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}


async def _auto_tag_doc(*, doc_id: str) -> dict[str, Any]:
    """Agent Day 12: auto-tag wrapper. Swallows any LLM failure so a doc
    upload never retries solely because of a tag-generation hiccup."""
    from app.services.auto_tagger import auto_tag_document

    try:
        return await auto_tag_document(document_id=doc_id)
    except Exception as exc:
        log.warning("[inngest] auto-tag failed: doc=%s err=%s", doc_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}


async def _smart_route_doc(*, doc_id: str, org_id: str) -> dict[str, Any]:
    """Agent2 Day 2 #33: propose a collection assignment from the doc's
    summary embedding. Best-effort — never fails the ingest pipeline."""
    from app.services.smart_routing import suggest_for_document

    try:
        return await suggest_for_document(document_id=doc_id, org_id=org_id)
    except Exception as exc:
        log.warning("[inngest] smart-route failed: doc=%s err=%s", doc_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}


# Extensions recognised as meeting transcripts. .vtt = WebVTT,
# .json = Microsoft Teams transcript export (other JSON docs are excluded
# by the agent's parser detection rather than this filter so a generic
# JSON upload doesn't get routed here), .txt tagged 'transcript' = manually
# uploaded Google Meet (or other bracketed-timestamp) plain-text transcript.
_MEETING_TRANSCRIPT_FILE_TYPES = {"vtt", "teams_transcript", "transcript"}


async def _maybe_route_meeting_transcript(
    *, doc_id: str, org_id: str, file_type: str | None,
) -> dict[str, Any]:
    """Agent Day 13: fan out a `meeting/transcript-uploaded` event when the
    upload is a transcript. Routing is keyed by file_type so the regular
    doc pipeline doesn't try to interpret every JSON.

    .vtt → WebVTT
    .json with file_type='teams_transcript' → Teams transcript export
        (the documents router sets that file_type when an admin uploads
        via the dedicated meeting-transcript path)
    .txt with file_type='transcript' → manually uploaded Google Meet (or
        other bracketed-timestamp) plain-text transcript
    """
    ft = (file_type or "").lower()
    if ft not in _MEETING_TRANSCRIPT_FILE_TYPES:
        return {"status": "skipped", "reason": "not_a_transcript"}
    try:
        await _inngest_client.send(
            inngest.Event(
                name="meeting/transcript-uploaded",
                data={"doc_id": doc_id, "org_id": org_id, "file_type": ft},
                id=f"meeting-transcript-{doc_id}",
            )
        )
        return {"status": "queued"}
    except Exception as exc:
        log.warning("[inngest] meeting routing failed: doc=%s err=%s", doc_id, exc)
        return {"status": "failed", "reason": str(exc)[:200]}


def _utcnow_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


# ── Standalone retrigger endpoints (called by admin UI / backfill scripts) ──

@_inngest_client.create_function(
    fn_id="rebuild-doc-summary",
    trigger=inngest.TriggerEvent(event="doc/rebuild-summary"),
    retries=1,
    concurrency=[
        inngest.Concurrency(limit=4, key="event.data.org_id", scope="fn"),
    ],
)
async def rebuild_doc_summary_fn(ctx: inngest.Context) -> dict[str, Any]:
    """Hand-fired event used by the admin backfill script and the document
    detail page's 'regenerate summary' action. Runs the same summarizer the
    upload pipeline uses, against an already-ready document."""
    data = ctx.event.data
    doc_id: str = data["doc_id"]
    return await ctx.step.run("summarize", lambda: _generate_doc_summary(doc_id=doc_id))


@_inngest_client.create_function(
    fn_id="rebuild-doc-toc",
    trigger=inngest.TriggerEvent(event="doc/rebuild-toc"),
    retries=1,
    concurrency=[
        inngest.Concurrency(limit=4, key="event.data.org_id", scope="fn"),
    ],
)
async def rebuild_doc_toc_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    doc_id: str = data["doc_id"]
    file_path: str = data["file_path"]
    file_type: str = data["file_type"]
    return await ctx.step.run(
        "extract-toc",
        lambda: _extract_doc_toc(doc_id=doc_id, file_path=file_path, file_type=file_type),
    )


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


# ── V5 #97 — Post-enrichment defaulting ─────────────────────────────────────
# Fires once after an admin completes the enrichment modal. Seeds AI
# instructions if the org hasn't set any yet — we never overwrite a customized
# instruction. Templates are not seeded here because the templates table
# already ships with built-in entries (see migration 018); we just want the
# tone of the assistant to match the org's primary use case from turn one.

_DEFAULT_AI_INSTRUCTIONS: dict[str, str] = {
    "hr_policies": (
        "This organization uses Contexo primarily for HR / People Ops "
        "questions. Quote the exact policy when possible, cite the document "
        "it came from, and never speculate about pay, benefits, or legal "
        "matters where the answer isn't in the retrieved context."
    ),
    "sales_enablement": (
        "This organization uses Contexo for sales enablement. Outputs "
        "should be persuasive, customer-focused, and grounded in the product "
        "positioning, pricing, and objection-handling docs we've uploaded."
    ),
    "customer_support": (
        "This organization uses Contexo for customer support. Replies "
        "should be empathetic, solution-focused, and reference the support "
        "policies and runbooks we've uploaded. Avoid making promises about "
        "refunds or SLAs that aren't already documented."
    ),
    "engineering": (
        "This organization uses Contexo for engineering. Outputs should "
        "be precise, technical, and reference the runbooks, postmortems, and "
        "architecture docs we've uploaded. Prefer terse, actionable answers."
    ),
}


@_inngest_client.create_function(
    fn_id="org-post-enrichment",
    trigger=inngest.TriggerEvent(event="org/post-enrichment"),
    retries=1,
)
async def org_post_enrichment_fn(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    use_case: str = (data.get("primary_use_case") or "").strip()
    industry: str = (data.get("industry") or "").strip()

    async def _seed_ai_instructions() -> dict[str, Any]:
        instruction = _DEFAULT_AI_INSTRUCTIONS.get(use_case)
        if not instruction:
            return {"seeded": False, "reason": "no_default_for_use_case"}
        svc = get_service_client()
        # Only seed if the org hasn't already written something; never clobber
        # a hand-tuned instruction.
        row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("ai_instructions")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        existing = (row.data or {}).get("ai_instructions") if row else None
        if existing and existing.strip():
            return {"seeded": False, "reason": "already_set"}
        await asyncio.to_thread(
            lambda: svc.table("organizations")
            .update({"ai_instructions": instruction})
            .eq("id", org_id)
            .execute()
        )
        return {"seeded": True, "use_case": use_case}

    async def _populate_recommended_documents() -> dict[str, Any]:
        # V3 #50 — drop a curated checklist onto the org row so the Documents
        # page widget can render it. Idempotent: only writes if the column is
        # still the default empty array, so a manual edit isn't clobbered.
        from app.services.recommendations import recommendations_for

        svc = get_service_client()
        row = await asyncio.to_thread(
            lambda: svc.table("organizations")
            .select("recommended_documents")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        existing = (row.data or {}).get("recommended_documents") if row else None
        if existing:
            return {"populated": False, "reason": "already_set"}

        recs = recommendations_for(primary_use_case=use_case, industry=industry)
        await asyncio.to_thread(
            lambda: svc.table("organizations")
            .update({"recommended_documents": recs})
            .eq("id", org_id)
            .execute()
        )
        return {"populated": True, "count": len(recs), "use_case": use_case or "general"}

    seeded = await ctx.step.run("seed-ai-instructions", _seed_ai_instructions)
    populated = await ctx.step.run(
        "populate-recommended-documents", _populate_recommended_documents
    )
    return {"ai_instructions": seeded, "recommendations": populated}


FUNCTIONS: list = [
    process_document,
    retry_failed_chunks,
    summarize_conversation_fn,
    recompute_document_health_fn,
    rebuild_doc_summary_fn,
    rebuild_doc_toc_fn,
    org_post_enrichment_fn,
]
