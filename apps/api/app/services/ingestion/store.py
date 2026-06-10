"""Idempotent persistence for chunks + embeddings, with per-chunk state.

Two-phase write:
    1. `persist_chunks_pending` inserts the full chunk set with
       `embedding_status='pending'`. Prior chunks for the doc are deleted
       first, so this remains the idempotent reset point.
    2. `record_embeddings` writes the embedding rows for a batch of chunks
       that just succeeded, and flips their status to `'embedded'`.
    3. `mark_chunks_failed` flips status to `'failed'` (with an error msg)
       for chunks whose embedding call exhausted retries.

This split lets the pipeline track per-chunk outcomes — needed for the
"partial document" UX where some chunks embedded and some didn't, and for
the targeted retry path that re-embeds only the failed chunks.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from supabase import Client

from .types import Chunk

log = logging.getLogger(__name__)

_INSERT_BATCH = 200  # Supabase REST has a ~1 MiB body limit; 200 chunks ≈ safe


@dataclass(frozen=True)
class PersistedChunk:
    """A chunk that's been written to the DB and has a server-assigned id."""

    id: str
    chunk: Chunk


async def persist_chunks_pending(
    chunks: list[Chunk],
    *,
    doc_id: str,
    org_id: str,
    client: Client,
) -> list[PersistedChunk]:
    """Replace this document's chunks with the new set, all in `pending` state.

    Returns the persisted chunks with their assigned ids so the caller can
    embed and then update status row-by-row.
    """
    await _delete_existing(client, doc_id)
    if not chunks:
        return []

    rows: list[dict[str, Any]] = []
    persisted: list[PersistedChunk] = []
    for ch in chunks:
        cid = str(uuid.uuid4())
        persisted.append(PersistedChunk(id=cid, chunk=ch))
        rows.append(
            {
                "id": cid,
                "org_id": org_id,
                "document_id": doc_id,
                "content": ch.content,
                "chunk_index": ch.chunk_index,
                "token_count": ch.token_count,
                "page_number": ch.page_number,
                "section_heading": ch.section_heading,
                "metadata": ch.metadata or {},
                "embedding_status": "pending",
                "retry_count": 0,
            }
        )
    await _batched_insert(client, "chunks", rows)
    return persisted


async def record_embeddings(
    items: list[tuple[PersistedChunk, list[float]]],
    *,
    org_id: str,
    client: Client,
) -> None:
    """Persist embeddings for a batch of chunks and flip their status.

    Both writes are batched. We treat the status flip as best-effort retryable:
    the embedding row is the source of truth; the status column is denormalised
    for fast filtering. A chunk with an embedding row but stale `pending`
    status is benign — vector search will still surface it.
    """
    if not items:
        return

    embedding_rows = [
        {"chunk_id": pc.id, "org_id": org_id, "embedding": vec}
        for pc, vec in items
    ]
    await _batched_insert(client, "embeddings", embedding_rows)

    ids = [pc.id for pc, _ in items]
    # Bulk update via .in_(); supabase-py issues one PATCH per call so we
    # batch the id list the same way we batch inserts.
    for start in range(0, len(ids), _INSERT_BATCH):
        batch_ids = ids[start : start + _INSERT_BATCH]
        await asyncio.to_thread(
            lambda b=batch_ids: client.table("chunks")
            .update({"embedding_status": "embedded", "embedding_error": None})
            .in_("id", b)
            .execute()
        )


async def mark_chunks_failed(
    chunk_ids: list[str],
    *,
    error: str,
    client: Client,
) -> None:
    """Flip a batch of chunks to `failed` with a truncated error message."""
    if not chunk_ids:
        return
    truncated = (error or "embedding failed")[:500]
    for start in range(0, len(chunk_ids), _INSERT_BATCH):
        batch = chunk_ids[start : start + _INSERT_BATCH]
        await asyncio.to_thread(
            lambda b=batch: client.table("chunks")
            .update({"embedding_status": "failed", "embedding_error": truncated})
            .in_("id", b)
            .execute()
        )


async def bump_retry_count(chunk_ids: list[str], *, client: Client) -> None:
    """Bump retry_count for each chunk we re-attempted. Best-effort."""
    if not chunk_ids:
        return
    # No bulk arithmetic update through PostgREST — read then write. Done
    # rarely (only on retry path) so the round-trip cost is fine.
    rows = await asyncio.to_thread(
        lambda: client.table("chunks").select("id, retry_count").in_("id", chunk_ids).execute()
    )
    for row in rows.data or []:
        cid = row["id"]
        current = int(row.get("retry_count") or 0)
        await asyncio.to_thread(
            lambda c=cid, n=current + 1: client.table("chunks")
            .update({"retry_count": n})
            .eq("id", c)
            .execute()
        )


async def fetch_failed_chunks(
    *,
    doc_id: str,
    client: Client,
) -> list[PersistedChunk]:
    """Return all chunks for a doc that are in `failed` state, ordered."""
    result = await asyncio.to_thread(
        lambda: client.table("chunks")
        .select(
            "id, content, chunk_index, token_count, page_number, "
            "section_heading, metadata"
        )
        .eq("document_id", doc_id)
        .eq("embedding_status", "failed")
        .order("chunk_index")
        .execute()
    )
    out: list[PersistedChunk] = []
    for row in result.data or []:
        out.append(
            PersistedChunk(
                id=row["id"],
                chunk=Chunk(
                    content=row["content"],
                    chunk_index=int(row["chunk_index"]),
                    token_count=int(row.get("token_count") or 0),
                    page_number=row.get("page_number"),
                    section_heading=row.get("section_heading"),
                    metadata=row.get("metadata") or {},
                ),
            )
        )
    return out


async def _delete_existing(client: Client, doc_id: str) -> None:
    await asyncio.to_thread(
        lambda: client.table("chunks").delete().eq("document_id", doc_id).execute()
    )


async def _batched_insert(client: Client, table: str, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _INSERT_BATCH):
        batch = rows[i : i + _INSERT_BATCH]
        await asyncio.to_thread(
            lambda b=batch: client.table(table).insert(b).execute()
        )
