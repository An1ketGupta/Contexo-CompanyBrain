"""Ingest a text-only document (no file in storage).

Used by Notion sync, email-forward inbound, and Drive's Google-Docs export
path. The regular `doc/uploaded` flow assumes there's a file in Supabase
Storage that the pipeline downloads + parses. Here, the integration already
holds the plaintext, so we skip parsing and feed the chunker directly.

Shape mirrors `services.ingestion.pipeline.process_document` so callers can
treat both as interchangeable: chunk → persist → embed → mark ready.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.database import get_service_client
from app.services.ingestion import (
    PipelineError,
    chunk_segments,
    mark_status,
    persist_chunks_pending,
)
from app.services.ingestion.embedder import get_embedder
from app.services.ingestion.pipeline import _embed_with_per_chunk_fallback
from app.services.ingestion.store import record_embeddings  # noqa: F401  (re-exported via helpers)
from app.services.ingestion.types import RawSegment

log = logging.getLogger(__name__)


async def ingest_text_document(
    *,
    doc_id: str,
    org_id: str,
    text: str,
    title: str | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Chunk + embed + persist already-extracted text.

    Idempotent w.r.t. doc_id: the documents row must already exist (typically
    upserted by the integration's sync function before queueing the event).

    `title` / `source_url` are propagated onto each segment's section_heading
    and metadata so retrieval-side citations can label the source even though
    no Storage file exists. Set by the Chrome extension's "Add to Brain" flow
    (V4 #32); legacy callers omit them and we fall back to no-context chunks.
    """
    text = (text or "").strip()
    if not text:
        await mark_status(doc_id, "failed", error_reason="Empty document body.")
        return {"status": "failed", "error": "empty"}

    svc = get_service_client()

    await mark_status(doc_id, "processing")

    try:
        seg_metadata: dict[str, Any] = {}
        if source_url:
            seg_metadata["source_url"] = source_url
        # One synthetic segment — the chunker handles overflow via its own
        # token-aware splitter, so a 50 KB Notion page chunks identically to
        # a 50 KB extracted-from-PDF body.
        segments = [
            RawSegment(
                content=text,
                section_heading=title or None,
                metadata=seg_metadata,
            )
        ]
        chunks = chunk_segments(segments)
        if not chunks:
            raise PipelineError("Chunker produced no output.")

        persisted = await persist_chunks_pending(
            chunks, doc_id=doc_id, org_id=org_id, client=svc
        )
        embedded, failed = await _embed_with_per_chunk_fallback(
            persisted, org_id=org_id, client=svc, embedder=get_embedder()
        )

        if embedded == 0:
            await mark_status(
                doc_id, "failed",
                error_reason="All chunks failed to embed.",
                embedding_stats={"embedded": 0, "failed": failed, "total": len(persisted)},
            )
            return {"status": "failed", "embedded": 0, "failed": failed}

        stats = {"embedded": embedded, "failed": failed, "total": len(persisted)} if failed else None
        await mark_status(doc_id, "ready", chunk_count=len(persisted), embedding_stats=stats)
        return {
            "status": "ok",
            "embedded": embedded,
            "failed": failed,
            "total": len(persisted),
        }
    except PipelineError as exc:
        await mark_status(doc_id, "failed", error_reason=str(exc))
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        log.exception("ingest_text_document failed for %s", doc_id)
        await mark_status(doc_id, "failed", error_reason=f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}


async def upsert_external_document(
    *,
    org_id: str,
    source: str,
    external_id: str,
    name: str,
    file_type: str,
    user_id: str | None = None,
) -> str:
    """Upsert a documents row keyed by (org_id, source, external_id).

    Used by Notion + Drive sync paths so re-poll of an already-ingested page
    refreshes its content rather than creating a duplicate. Returns the
    document UUID.
    """
    svc = get_service_client()

    def _run() -> str:
        # Try existing first — `upsert` would also work but PostgREST's upsert
        # requires an explicit unique constraint and the conditional `external_id
        # IS NOT NULL` makes that fragile. Two round trips is fine.
        existing = (
            svc.table("documents")
            .select("id")
            .eq("org_id", org_id)
            .eq("source", source)
            .eq("external_id", external_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            doc_id = existing.data["id"]
            svc.table("documents").update(
                {"name": name, "status": "processing", "chunk_count": None}
            ).eq("id", doc_id).execute()
            # Wipe prior chunks so the next embed run doesn't mix old +
            # new content. Cascade kills embeddings too.
            svc.table("chunks").delete().eq("document_id", doc_id).execute()
            return doc_id

        import uuid as _uuid
        new_id = str(_uuid.uuid4())
        svc.table("documents").insert(
            {
                "id": new_id,
                "org_id": org_id,
                "name": name,
                "file_path": f"virtual/{source}/{external_id}",
                "file_type": file_type,
                "status": "processing",
                "source": source,
                "external_id": external_id,
                "created_by": user_id,
            }
        ).execute()
        return new_id

    return await asyncio.to_thread(_run)
