"""End-to-end ingestion: parse → chunk → persist (pending) → embed batch-by-batch.

Why the batch + per-chunk fallback dance:
    Gemini's embed_content takes batches of up to 100. A single bad input
    (e.g. a chunk that trips a server-side safety filter) fails the *whole*
    batch — and naive retries would re-fail forever. Falling back to per-
    chunk embedding on batch failure isolates the offender so the rest of
    the document still gets vectors. On the happy path (no bad chunks), the
    batch call rate is unchanged.

Outcome shape (`ProcessStats`) is what `mark_status` uses to decide between
'ready' (any chunks embedded) and 'failed' (none) and to attach the
embedded/failed/total triple to `documents.metadata.embedding` for the UI's
partial-state badge.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from supabase import Client

from app.database import get_service_client
from app.services.langfuse import observe

from .chunker import chunk_segments
from .embedder import Embedder, EmbeddingError, _augment_for_embedding, get_embedder
from .parser import EmptyDocumentError, ParseError, parse_document
from .store import (
    PersistedChunk,
    bump_retry_count,
    fetch_failed_chunks,
    mark_chunks_failed,
    persist_chunks_pending,
    record_embeddings,
)

log = logging.getLogger(__name__)

STORAGE_BUCKET = "document"

# Smaller than the embedder's hard batch_size — keeps per-batch latency
# bounded and limits the blast radius when one batch trips a safety filter.
_EMBED_BATCH = 50


class PipelineError(Exception):
    """Wraps any failure during ingestion with a user-readable reason."""


@dataclass(frozen=True)
class ProcessStats:
    doc_id: str
    chunk_count: int
    embedded: int
    failed: int
    total_tokens: int


@observe(name="ingest_document")
async def process_document(
    *,
    doc_id: str,
    org_id: str,
    file_bytes: bytes,
    file_type: str,
    client: Client | None = None,
    document_version_id: str | None = None,
) -> ProcessStats:
    """Run the full pipeline. Returns stats. Raises PipelineError on hard failure."""
    client = client or get_service_client()

    try:
        log.info("[%s] parsing %s (%d bytes)", doc_id, file_type, len(file_bytes))
        segments = parse_document(file_bytes, file_type)
        log.info("[%s] parsed %d segments", doc_id, len(segments))

        chunks = chunk_segments(segments)
        log.info("[%s] produced %d chunks", doc_id, len(chunks))
        if not chunks:
            raise PipelineError("Document yielded no chunks after splitting.")

        persisted = await persist_chunks_pending(
            chunks,
            doc_id=doc_id,
            org_id=org_id,
            client=client,
            document_version_id=document_version_id,
        )
        log.info("[%s] persisted %d pending chunks", doc_id, len(persisted))

        embedded, failed = await _embed_with_per_chunk_fallback(
            persisted, org_id=org_id, client=client
        )
        log.info(
            "[%s] embedded %d / %d chunks (failed=%d)",
            doc_id, embedded, len(persisted), failed,
        )

        return ProcessStats(
            doc_id=doc_id,
            chunk_count=len(persisted),
            embedded=embedded,
            failed=failed,
            total_tokens=sum(pc.chunk.token_count for pc in persisted),
        )
    except EmptyDocumentError as exc:
        raise PipelineError(str(exc)) from exc
    except ParseError as exc:
        raise PipelineError(f"Could not parse document: {exc}") from exc
    except PipelineError:
        raise
    except Exception as exc:
        log.exception("[%s] pipeline failed", doc_id)
        raise PipelineError(f"Ingestion failed: {type(exc).__name__}: {exc}") from exc


@observe(name="retry_failed_chunks")
async def reembed_failed_chunks(
    *,
    doc_id: str,
    org_id: str,
    client: Client | None = None,
) -> ProcessStats:
    """Targeted retry: only re-embed chunks currently in `failed` state.

    Used by the user-visible "Retry" button when a document is in partial
    state, and by the Inngest doc/retry-chunks handler.
    """
    client = client or get_service_client()
    targets = await fetch_failed_chunks(doc_id=doc_id, client=client)
    if not targets:
        return ProcessStats(doc_id=doc_id, chunk_count=0, embedded=0, failed=0, total_tokens=0)

    await bump_retry_count([pc.id for pc in targets], client=client)
    embedded, failed = await _embed_with_per_chunk_fallback(
        targets, org_id=org_id, client=client
    )
    return ProcessStats(
        doc_id=doc_id,
        chunk_count=len(targets),
        embedded=embedded,
        failed=failed,
        total_tokens=sum(pc.chunk.token_count for pc in targets),
    )


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
    embedding_stats: dict[str, int] | None = None,
    client: Client | None = None,
) -> None:
    """Update document.status (and metadata.error_reason / metadata.embedding)."""
    client = client or get_service_client()
    update: dict[str, Any] = {"status": status}
    if chunk_count is not None:
        update["chunk_count"] = chunk_count

    if error_reason is not None or embedding_stats is not None:
        existing = await asyncio.to_thread(
            lambda: client.table("documents").select("metadata").eq("id", doc_id).maybe_single().execute()
        )
        metadata = (existing.data.get("metadata") if existing.data else None) or {}
        if error_reason is not None:
            metadata["error_reason"] = error_reason
        if embedding_stats is not None:
            metadata["embedding"] = embedding_stats
        update["metadata"] = metadata

    await asyncio.to_thread(
        lambda: client.table("documents").update(update).eq("id", doc_id).execute()
    )


# ── Internal: batch-then-per-chunk embedding with status tracking ────────────

async def _embed_with_per_chunk_fallback(
    chunks: list[PersistedChunk],
    *,
    org_id: str,
    client: Client,
    embedder: Embedder | None = None,
) -> tuple[int, int]:
    """Embed `chunks`, batching for speed and falling back per-chunk on failure.

    Returns (embedded_count, failed_count). Status updates land in the chunks
    table as each batch resolves so the UI sees progress even on long runs.
    """
    if not chunks:
        return 0, 0
    embedder = embedder or get_embedder()

    embedded_total = 0
    failed_total = 0

    for start in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[start : start + _EMBED_BATCH]
        texts = [_augment_for_embedding(pc.chunk) for pc in batch]
        try:
            vectors = await embedder.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:
            log.warning(
                "embed batch of %d failed (%s); falling back per-chunk",
                len(batch), exc,
            )
            ok, bad = await _embed_one_by_one(batch, org_id=org_id, client=client, embedder=embedder)
            embedded_total += ok
            failed_total += bad
            continue

        if len(vectors) != len(batch):
            # Provider returned a partial/extra response — treat as batch failure
            # and let the per-chunk loop sort it out.
            log.warning(
                "embed batch count mismatch (got %d, sent %d) — falling back",
                len(vectors), len(batch),
            )
            ok, bad = await _embed_one_by_one(batch, org_id=org_id, client=client, embedder=embedder)
            embedded_total += ok
            failed_total += bad
            continue

        await record_embeddings(
            list(zip(batch, vectors, strict=True)),
            org_id=org_id,
            client=client,
        )
        embedded_total += len(batch)

    return embedded_total, failed_total


async def _embed_one_by_one(
    chunks: list[PersistedChunk],
    *,
    org_id: str,
    client: Client,
    embedder: Embedder,
) -> tuple[int, int]:
    """Per-chunk fallback: isolate the offender so the rest still vectors."""
    embedded = 0
    failed_ids: list[str] = []
    last_error = ""

    for pc in chunks:
        text = _augment_for_embedding(pc.chunk)
        try:
            vectors = await embedder.embed_texts([text], task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log.warning("chunk %s failed individually: %s", pc.id, last_error)
            failed_ids.append(pc.id)
            continue

        if not vectors:
            last_error = "embedding returned no vectors"
            failed_ids.append(pc.id)
            continue

        try:
            await record_embeddings(
                [(pc, vectors[0])], org_id=org_id, client=client
            )
            embedded += 1
        except Exception as exc:
            log.exception("persist embedding failed for chunk %s", pc.id)
            last_error = f"persist: {exc}"
            failed_ids.append(pc.id)

    if failed_ids:
        await mark_chunks_failed(failed_ids, error=last_error or "embedding failed", client=client)

    return embedded, len(failed_ids)
