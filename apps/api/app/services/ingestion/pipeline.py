"""End-to-end ingestion: parse → chunk → embed → store.

`process_document` is the function Inngest will wrap on Day 8. It's also safe to
call directly from a script for local testing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from supabase import Client

from app.database import get_service_client

from .chunker import chunk_segments
from .embedder import embed_chunks
from .parser import EmptyDocumentError, ParseError, parse_document
from .store import persist_chunks

log = logging.getLogger(__name__)

STORAGE_BUCKET = "document"


class PipelineError(Exception):
    """Wraps any failure during ingestion with a user-readable reason."""


async def process_document(
    *,
    doc_id: str,
    org_id: str,
    file_bytes: bytes,
    file_type: str,
    client: Client | None = None,
) -> dict[str, Any]:
    """Run the full pipeline. Returns stats. Raises PipelineError on failure."""
    client = client or get_service_client()

    try:
        log.info("[%s] parsing %s (%d bytes)", doc_id, file_type, len(file_bytes))
        segments = parse_document(file_bytes, file_type)
        log.info("[%s] parsed %d segments", doc_id, len(segments))

        chunks = chunk_segments(segments)
        log.info("[%s] produced %d chunks", doc_id, len(chunks))
        if not chunks:
            raise PipelineError("Document yielded no chunks after splitting.")

        embedded = await embed_chunks(chunks)
        log.info("[%s] embedded %d chunks", doc_id, len(embedded))

        persisted = await persist_chunks(
            embedded, doc_id=doc_id, org_id=org_id, client=client
        )
        log.info("[%s] persisted %d chunks", doc_id, persisted)

        return {
            "doc_id": doc_id,
            "chunk_count": persisted,
            "total_tokens": sum(ec.chunk.token_count for ec in embedded),
        }
    except EmptyDocumentError as exc:
        raise PipelineError(str(exc)) from exc
    except ParseError as exc:
        raise PipelineError(f"Could not parse document: {exc}") from exc
    except Exception as exc:
        log.exception("[%s] pipeline failed", doc_id)
        raise PipelineError(f"Ingestion failed: {type(exc).__name__}: {exc}") from exc


async def download_from_storage(file_path: str, client: Client | None = None) -> bytes:
    """Fetch the raw file from the Supabase 'document' bucket."""
    client = client or get_service_client()
    return await asyncio.to_thread(
        lambda: client.storage.from_(STORAGE_BUCKET).download(file_path)
    )


async def mark_status(
    doc_id: str,
    status: str,
    *,
    chunk_count: int | None = None,
    error_reason: str | None = None,
    client: Client | None = None,
) -> None:
    """Update document.status (and chunk_count / metadata.error_reason where given)."""
    client = client or get_service_client()
    update: dict[str, Any] = {"status": status}
    if chunk_count is not None:
        update["chunk_count"] = chunk_count
    if error_reason is not None:
        existing = await asyncio.to_thread(
            lambda: client.table("documents").select("metadata").eq("id", doc_id).maybe_single().execute()
        )
        metadata = (existing.data.get("metadata") if existing.data else None) or {}
        metadata["error_reason"] = error_reason
        update["metadata"] = metadata

    await asyncio.to_thread(
        lambda: client.table("documents").update(update).eq("id", doc_id).execute()
    )
