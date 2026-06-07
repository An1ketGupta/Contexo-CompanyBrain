"""Idempotent persistence for chunks + embeddings.

`persist_chunks` is safe to call multiple times for the same doc_id:
prior chunks (and their cascading embeddings) are deleted first, then the new
set is inserted in batches.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from supabase import Client

from .types import EmbeddedChunk

log = logging.getLogger(__name__)

_INSERT_BATCH = 200  # Supabase REST has a ~1 MiB body limit; 200 chunks ≈ safe


async def persist_chunks(
    embedded: list[EmbeddedChunk],
    *,
    doc_id: str,
    org_id: str,
    client: Client,
) -> int:
    """Replace this document's chunks atomically-from-the-user's-perspective.

    Returns the number of chunks written.
    """
    await _delete_existing(client, doc_id)

    if not embedded:
        return 0

    chunk_rows, chunk_ids = _build_chunk_rows(embedded, doc_id=doc_id, org_id=org_id)
    await _batched_insert(client, "chunks", chunk_rows)

    embedding_rows = _build_embedding_rows(chunk_ids, embedded, org_id=org_id)
    await _batched_insert(client, "embeddings", embedding_rows)

    return len(embedded)


async def _delete_existing(client: Client, doc_id: str) -> None:
    await asyncio.to_thread(
        lambda: client.table("chunks").delete().eq("document_id", doc_id).execute()
    )


def _build_chunk_rows(
    embedded: list[EmbeddedChunk], *, doc_id: str, org_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    ids: list[str] = []
    for ec in embedded:
        cid = str(uuid.uuid4())
        ids.append(cid)
        rows.append(
            {
                "id": cid,
                "org_id": org_id,
                "document_id": doc_id,
                "content": ec.chunk.content,
                "chunk_index": ec.chunk.chunk_index,
                "token_count": ec.chunk.token_count,
                "page_number": ec.chunk.page_number,
                "section_heading": ec.chunk.section_heading,
                "metadata": ec.chunk.metadata or {},
            }
        )
    return rows, ids


def _build_embedding_rows(
    chunk_ids: list[str], embedded: list[EmbeddedChunk], *, org_id: str
) -> list[dict[str, Any]]:
    return [
        {"chunk_id": cid, "org_id": org_id, "embedding": ec.embedding}
        for cid, ec in zip(chunk_ids, embedded, strict=True)
    ]


async def _batched_insert(client: Client, table: str, rows: list[dict[str, Any]]) -> None:
    for i in range(0, len(rows), _INSERT_BATCH):
        batch = rows[i : i + _INSERT_BATCH]
        await asyncio.to_thread(
            lambda b=batch: client.table(table).insert(b).execute()
        )
