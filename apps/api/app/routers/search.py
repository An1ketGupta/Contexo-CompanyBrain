"""Debug search endpoint.

Lets us hit each retrieval branch directly from a browser/curl during
development — useful for tuning chunking, embeddings, and FTS without
going through the full LLM tool-use loop. The LLM's `search_company_knowledge`
tool (Day 12/13) will call the same underlying retrievers; this endpoint
shares zero state with it beyond the adapter classes.

Considered removing once the chat pipeline lands; keeping it gated by JWT +
debug-only docs feels worth the small surface area for ongoing quality work.
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import verify_jwt
from app.database import get_user_client
from app.services.retrieval import (
    SearchHit,
    fts_search,
    hybrid_search,
    vector_search,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

SearchMode = Literal["hybrid", "vector", "fts"]


@router.get("")
async def debug_search(
    q: str = Query(..., min_length=1, max_length=512, description="Search query"),
    mode: SearchMode = Query("hybrid"),
    k: int = Query(10, ge=1, le=50),
    doc_id: str | None = Query(None, description="Optional document scope filter"),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id: str | None = current_user["org_id"]
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization found.",
        )

    client = get_user_client(current_user["token"])

    try:
        if mode == "vector":
            hits = await vector_search(q, org_id, client, k=k, document_id=doc_id)
        elif mode == "fts":
            hits = await fts_search(q, org_id, client, k=k, document_id=doc_id)
        else:
            hits = await hybrid_search(q, org_id, client, k=k, document_id=doc_id)
    except Exception as exc:
        log.exception("Search failed (mode=%s, org=%s): %s", mode, org_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed. See server logs.",
        ) from exc

    return {
        "query": q,
        "mode": mode,
        "count": len(hits),
        "hits": [_serialize(h) for h in hits],
    }


def _serialize(hit: SearchHit) -> dict[str, Any]:
    return {
        "chunk_id": hit.chunk_id,
        "document_id": hit.document_id,
        "document_name": hit.document_name,
        "chunk_index": hit.chunk_index,
        "page_number": hit.page_number,
        "section_heading": hit.section_heading,
        "similarity": hit.similarity,
        "content": hit.content,
        "snippet": hit.snippet,
        "match_sources": list(hit.match_sources),
        "vector_rank": hit.vector_rank,
        "fts_rank": hit.fts_rank,
        "rrf_score": hit.rrf_score,
    }
