"""Slack content → NirnayaIQ documents.

This module is the "translator layer" between Slack's data model (pins,
canvases, files) and our `documents` table. Each function takes already-
fetched Slack content (from slack_inbound.*) and either:

  * queues a `doc/process-text` event for text content (pins, canvases,
    text-shaped files like markdown / HTML)
  * uploads the file to Supabase Storage and queues `doc/uploaded` for
    binary file types (PDF, DOCX, PPTX, XLSX) so they ride the same
    PyMuPDF/python-docx pipeline that handles direct uploads.

Idempotency: external_id keys are deterministic — re-running the same
ingest just updates the existing documents row in place (via
upsert_external_document) and re-runs the chunk/embed pipeline. Safe to
fire from both backfill and live events.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.services.integrations import slack_inbound
from app.services.integrations.text_ingest import upsert_external_document

log = logging.getLogger(__name__)

SOURCE_TAG = "slack"

# Slack file types → our internal file_type label. We split into two buckets:
# "text" (we can extract bytes-as-text and shortcut into doc/process-text)
# and "binary" (we Storage-upload and ride doc/uploaded).
_TEXT_FILE_TYPES: dict[str, str] = {
    "markdown": "md",
    "post": "md",       # legacy Slack "posts" (pre-canvas)
    "text": "txt",
    "html": "html",
    "csv": "csv",
}
_BINARY_FILE_TYPES: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
}

STORAGE_BUCKET = "document"


# ── External ID helpers ─────────────────────────────────────────────────────


def pin_external_id(team_id: str, channel_id: str, ts: str) -> str:
    return f"slack:{team_id}:{channel_id}:{ts}"


def canvas_external_id(team_id: str, canvas_id: str) -> str:
    return f"slack:{team_id}:canvas:{canvas_id}"


def file_external_id(team_id: str, file_id: str) -> str:
    return f"slack:{team_id}:file:{file_id}"


# ── Pins ────────────────────────────────────────────────────────────────────


async def ingest_pin(
    *,
    org_id: str,
    team_id: str,
    channel_id: str,
    channel_name: str | None,
    ts: str,
) -> dict[str, Any]:
    """Fetch a pinned message + its thread context and queue ingest.

    The doc body is shaped as a readable transcript: the pin first, then any
    thread replies in chronological order with author attribution. Authors
    are resolved via users.info (cached) — if that scope was withheld we
    fall back to raw user IDs, which still embed fine.
    """
    msg = await slack_inbound.fetch_message(
        org_id=org_id, channel_id=channel_id, ts=ts
    )
    if not msg or not (msg.get("text") or "").strip():
        return {"status": "skipped", "reason": "empty_or_missing"}

    # Pull thread if the pin sits on a parent — thread_ts == ts means this
    # message kicked off a thread, so we want the whole thing.
    thread_ts = msg.get("thread_ts") or ts
    thread = await slack_inbound.fetch_thread(
        org_id=org_id, channel_id=channel_id, parent_ts=thread_ts
    )
    # If the pinned message is a thread reply (not the parent), include the
    # parent for context.
    if msg.get("thread_ts") and msg["thread_ts"] != ts and not thread:
        thread = [msg]

    # Build doc body: parent → replies, author-prefixed.
    parts: list[str] = []
    if channel_name:
        parts.append(f"# Pinned in #{channel_name}")
    parts.append("")
    used = thread or [msg]
    for m in used:
        author = await _author_label(org_id=org_id, user_id=m.get("user_id"))
        text = (m.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"**{author}:** {text}")
        parts.append("")
    body = "\n".join(parts).strip()
    if not body:
        return {"status": "skipped", "reason": "empty_body"}

    name = _pin_doc_name(channel_name, msg.get("text") or "")
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=pin_external_id(team_id, channel_id, ts),
        name=name,
        file_type="md",
    )
    await _queue_text(doc_id=doc_id, org_id=org_id, text=body, title=name)
    return {"status": "queued", "doc_id": doc_id}


async def remove_pin_document(
    *, org_id: str, team_id: str, channel_id: str, ts: str
) -> dict[str, Any]:
    """Mark a pin's document as archived when the pin is removed.

    We don't hard-delete — admins commonly unpin/repin during cleanup, and
    a 1-day grace gives them a path back. The status flips to 'archived'
    which the retrieval layer treats as invisible to search.
    """
    svc = get_service_client()
    ext_id = pin_external_id(team_id, channel_id, ts)

    def _run() -> int:
        result = (
            svc.table("documents")
            .update({"status": "archived"})
            .eq("org_id", org_id)
            .eq("source", SOURCE_TAG)
            .eq("external_id", ext_id)
            .execute()
        )
        return len(result.data or [])

    n = await asyncio.to_thread(_run)
    return {"status": "ok", "archived": n}


# ── Canvases ────────────────────────────────────────────────────────────────


async def ingest_canvas(
    *,
    org_id: str,
    team_id: str,
    channel_id: str | None,
    channel_name: str | None,
    canvas_id: str,
    title: str | None = None,
) -> dict[str, Any]:
    """Fetch canvas markdown and queue ingest.

    Falls back to channel-derived title if the canvas itself has none.
    Canvases are Slack's first-class "doc" surface — these are nearly
    always high-signal so we don't bother filtering by length.
    """
    text = await slack_inbound.fetch_canvas_content(
        org_id=org_id, canvas_file_id=canvas_id
    )
    if not text or not text.strip():
        return {"status": "skipped", "reason": "empty"}

    name = title or (f"Canvas · #{channel_name}" if channel_name else f"Canvas {canvas_id}")
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=canvas_external_id(team_id, canvas_id),
        name=name,
        file_type="md",
    )
    await _queue_text(doc_id=doc_id, org_id=org_id, text=text, title=name)
    return {"status": "queued", "doc_id": doc_id}


# ── Files ───────────────────────────────────────────────────────────────────


async def ingest_file(
    *,
    org_id: str,
    team_id: str,
    channel_id: str | None,
    channel_name: str | None,
    file: dict[str, Any],
) -> dict[str, Any]:
    """Ingest a file shared in a channel.

    Text-shaped files (markdown / txt / html / csv) get the fast path —
    we hold the bytes, decode, and queue `doc/process-text`. Binary office
    docs get uploaded into Supabase Storage and queued as `doc/uploaded`
    so they ride the existing parser pipeline.
    """
    file_id = file.get("id")
    if not file_id:
        return {"status": "skipped", "reason": "no_file_id"}
    filetype = (file.get("filetype") or "").lower()
    name = file.get("title") or file.get("name") or f"Slack file {file_id}"

    # Decide bucket
    if filetype in _TEXT_FILE_TYPES:
        return await _ingest_text_file(
            org_id=org_id,
            team_id=team_id,
            channel_name=channel_name,
            file=file,
            internal_type=_TEXT_FILE_TYPES[filetype],
            name=name,
        )
    if filetype in _BINARY_FILE_TYPES:
        return await _ingest_binary_file(
            org_id=org_id,
            team_id=team_id,
            channel_name=channel_name,
            file=file,
            internal_type=_BINARY_FILE_TYPES[filetype],
            name=name,
        )

    log.info(
        "slack_file_skip_unsupported_type file=%s filetype=%s", file_id, filetype
    )
    return {"status": "skipped", "reason": f"unsupported_filetype:{filetype}"}


async def _ingest_text_file(
    *,
    org_id: str,
    team_id: str,
    channel_name: str | None,
    file: dict[str, Any],
    internal_type: str,
    name: str,
) -> dict[str, Any]:
    raw = await slack_inbound.download_file(org_id=org_id, file=file)
    if raw is None:
        return {"status": "failed", "reason": "download_failed"}
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("slack_text_decode_failed %s: %s", file.get("id"), exc)
        return {"status": "failed", "reason": "decode_failed"}

    display_name = name if not channel_name else f"{name} · #{channel_name}"
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=file_external_id(team_id, file["id"]),
        name=display_name,
        file_type=internal_type,
    )
    await _queue_text(doc_id=doc_id, org_id=org_id, text=text, title=display_name)
    return {"status": "queued", "doc_id": doc_id}


async def _ingest_binary_file(
    *,
    org_id: str,
    team_id: str,
    channel_name: str | None,
    file: dict[str, Any],
    internal_type: str,
    name: str,
) -> dict[str, Any]:
    raw = await slack_inbound.download_file(org_id=org_id, file=file)
    if raw is None:
        return {"status": "failed", "reason": "download_failed"}

    display_name = name if not channel_name else f"{name} · #{channel_name}"
    doc_id = await upsert_external_document(
        org_id=org_id,
        source=SOURCE_TAG,
        external_id=file_external_id(team_id, file["id"]),
        name=display_name,
        file_type=internal_type,
    )
    safe_name = name.replace("/", "_").replace("..", "_").strip() or "slack_file"
    storage_path = f"orgs/{org_id}/docs/{doc_id}/{safe_name}"

    svc = get_service_client()

    def _upload() -> None:
        # `upsert=True` covers the re-ingest case (admin removes + re-adds
        # the channel). storage3 expects `file_options` keys as strings.
        svc.storage.from_(STORAGE_BUCKET).upload(
            path=storage_path,
            file=raw,
            file_options={
                "content-type": file.get("mimetype") or "application/octet-stream",
                "upsert": "true",
            },
        )

    try:
        await asyncio.to_thread(_upload)
    except Exception as exc:
        log.warning("slack_file_storage_upload_failed %s: %s", file.get("id"), exc)
        return {"status": "failed", "reason": "storage_upload_failed"}

    # Patch the documents row with the actual file_path now that the upload
    # succeeded — upsert_external_document set a virtual path placeholder.
    def _patch_path() -> None:
        svc.table("documents").update({"file_path": storage_path}).eq(
            "id", doc_id
        ).execute()

    await asyncio.to_thread(_patch_path)

    # Ride the regular ingest pipeline.
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="doc/uploaded",
            data={"doc_id": doc_id, "org_id": org_id},
            id=f"slack-file-{doc_id}-{file.get('updated') or file.get('created') or ''}",
        )
    )
    return {"status": "queued", "doc_id": doc_id}


# ── Internal helpers ────────────────────────────────────────────────────────


async def _queue_text(
    *, doc_id: str, org_id: str, text: str, title: str | None
) -> None:
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="doc/process-text",
            data={
                "doc_id": doc_id,
                "org_id": org_id,
                "text": text,
                "title": title,
            },
            id=f"slack-{doc_id}",
        )
    )


def _pin_doc_name(channel_name: str | None, raw_text: str) -> str:
    """A short, identifiable name for the documents.name column.

    Slack messages can be long — we want a sources-panel-friendly label.
    Strip newlines, cap at 80 chars, and prefix with the channel for
    disambiguation when several pins have similar text.
    """
    snippet = " ".join((raw_text or "").split())
    if len(snippet) > 80:
        snippet = snippet[:77] + "…"
    if not snippet:
        snippet = "Pinned message"
    if channel_name:
        return f"#{channel_name}: {snippet}"
    return snippet


async def _author_label(*, org_id: str, user_id: str | None) -> str:
    if not user_id:
        return "Unknown"
    user = await slack_inbound.resolve_user(org_id=org_id, user_id=user_id)
    if not user:
        return user_id  # fall back gracefully
    return (
        user.get("display_name")
        or user.get("real_name")
        or user.get("name")
        or user_id
    )
