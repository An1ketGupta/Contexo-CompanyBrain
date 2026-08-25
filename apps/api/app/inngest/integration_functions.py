"""Inngest functions for Day-14 integration polling syncs.

Each integration (Google Drive, Notion) has a polling cron that fans out
per-org sync jobs. The actual sync logic lives in the integration service
modules; this file just provides the trigger/fan-out plumbing so Inngest's
scheduling and retry policy is the source of truth for cadence.

The functions are configured to skip cleanly when the integration's third-
party credentials aren't set (env-gated). That way deploying without the
OAuth secrets doesn't spam the Inngest logs with crashes.
"""
from __future__ import annotations

import logging
from typing import Any

import inngest

from app.config import get_settings
from app.inngest.client import get_inngest_client

log = logging.getLogger(__name__)

_inngest_client = get_inngest_client()


# ── Google Drive: poll every 5 minutes ──────────────────────────────────────

@_inngest_client.create_function(
    fn_id="drive-poll-sync",
    trigger=inngest.TriggerCron(cron="*/5 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def drive_poll_sync(ctx: inngest.Context) -> dict[str, Any]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return {"status": "skipped", "reason": "google_oauth_not_configured"}

    # Lazy import — keeps the integration service modules off the import path
    # when Drive isn't configured (saves boot time + avoids pulling googleapi
    # client dependencies when not in use).
    from app.services.integrations.drive import poll_all_integrations

    result = await ctx.step.run("drive-sync-all", poll_all_integrations)
    return result


# ── Google Meet transcripts: poll every 15 minutes ──────────────────────────

@_inngest_client.create_function(
    fn_id="meet-transcript-poll-sync",
    trigger=inngest.TriggerCron(cron="*/15 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def meet_transcript_poll_sync(ctx: inngest.Context) -> dict[str, Any]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return {"status": "skipped", "reason": "google_oauth_not_configured"}

    from app.services.integrations.google_meet_transcripts import poll_all_users

    result = await ctx.step.run("meet-transcript-sync-all", poll_all_users)
    return result


# ── Support mailbox: poll every 2 minutes ───────────────────────────────────
#
# Tighter than the knowledge-ingest polls above: this is the customer support
# agent's inbound path, and a customer waiting on a reply notices minutes.

@_inngest_client.create_function(
    fn_id="support-mailbox-poll-sync",
    trigger=inngest.TriggerCron(cron="*/2 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def support_mailbox_poll_sync(ctx: inngest.Context) -> dict[str, Any]:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        return {"status": "skipped", "reason": "google_oauth_not_configured"}

    from app.services.integrations.support_mailbox import poll_all_support_inboxes

    result = await ctx.step.run("support-mailbox-poll-all", poll_all_support_inboxes)
    return result


# ── Notion: poll every 10 minutes ────────────────────────────────────────────

@_inngest_client.create_function(
    fn_id="notion-poll-sync",
    trigger=inngest.TriggerCron(cron="*/10 * * * *"),
    concurrency=[inngest.Concurrency(limit=1)],
)
async def notion_poll_sync(ctx: inngest.Context) -> dict[str, Any]:
    settings = get_settings()
    if not settings.notion_client_id or not settings.notion_client_secret:
        return {"status": "skipped", "reason": "notion_oauth_not_configured"}

    from app.services.integrations.notion import poll_all_integrations

    result = await ctx.step.run("notion-sync-all", poll_all_integrations)
    return result


# ── Generic "process-text-document" — used by integrations + email forward ─

@_inngest_client.create_function(
    fn_id="process-text-document",
    trigger=inngest.TriggerEvent(event="doc/process-text"),
    retries=3,
    concurrency=[inngest.Concurrency(limit=3, key="event.data.org_id", scope="fn")],
)
async def process_text_document(ctx: inngest.Context) -> dict[str, Any]:
    """Ingest a document whose raw text is already in memory.

    Used by:
        * Notion page sync (extracted text)
        * Email forward inbound (subject + body)
        * Drive Google-Docs sync (exported plaintext)

    For PDF/DOCX uploads, the regular `doc/uploaded` path still runs — we
    only reach here when there's no file in storage, just text we want
    chunked + embedded.
    """
    from app.services.integrations.text_ingest import ingest_text_document

    data = ctx.event.data
    result = await ctx.step.run(
        "ingest-text",
        lambda: ingest_text_document(
            doc_id=data["doc_id"],
            org_id=data["org_id"],
            text=data["text"],
            # Optional context fields (V4 #32 / Chrome Extension). The Notion
            # + email-forward paths can omit them; .get() preserves their
            # original event shape.
            title=data.get("title"),
            source_url=data.get("source_url"),
        ),
    )
    return result


# ── Generic external binary-document handler ───────────────────────────────

@_inngest_client.create_function(
    fn_id="process-binary-external",
    trigger=inngest.TriggerEvent(event="doc/process-binary-external"),
    retries=3,
    concurrency=[inngest.Concurrency(limit=4, key="event.data.org_id", scope="fn")],
)
async def process_binary_external(ctx: inngest.Context) -> dict[str, Any]:
    from app.services.integrations._unified import download_and_process_binary

    data = ctx.event.data
    return await ctx.step.run(
        "download-and-process",
        lambda: download_and_process_binary(
            doc_id=data["doc_id"],
            org_id=data["org_id"],
            file_type=data["file_type"],
            download_url=data["download_url"],
            auth_header=data.get("auth_header") or "",
        ),
    )


FUNCTIONS = [
    drive_poll_sync,
    meet_transcript_poll_sync,
    support_mailbox_poll_sync,
    notion_poll_sync,
    process_text_document,
    process_binary_external,
]
