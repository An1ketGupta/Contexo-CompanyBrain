"""Vector search via pgvector.

Embeds the query once and dispatches to the `vector_search` SQL function.
Same embedder, same dims, same L2 normalization as the ingest path — so the
query and stored vectors share an identical metric space.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any, Protocol

from supabase import Client

from app.services.ingestion.embedder import Embedder, get_embedder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchHit:
    """A single search result. Shape mirrors the SQL function's return columns.

    `similarity` is the canonical primary score and is always populated:
        • vector branch → cosine similarity in [0, 1]
        • FTS branch    → ts_rank_cd normalized to [0, 1)
        • hybrid branch → RRF score (small, unbounded above, ordering-only)
    Branch-specific fields below stay None on paths that don't fill them, so
    downstream code can treat a SearchHit uniformly regardless of source.
    """

    chunk_id: str
    content: str
    document_id: str
    document_name: str
    chunk_index: int
    page_number: int | None
    section_heading: str | None
    similarity: float
    # FTS-only: highlighted excerpt from ts_headline (consumed by the citations UI).
    snippet: str | None = None
    # Hybrid-only: which branches contributed, plus per-branch ranks for debugging.
    match_sources: tuple[str, ...] = ()
    vector_rank: int | None = None
    fts_rank: int | None = None
    rrf_score: float | None = None
    # Raw cosine from the vector branch. Survives RRF fusion (which clobbers
    # `similarity` with the fused score) so the confidence calculation in
    # task_chain has a true [0,1] interpretable signal to aggregate on.
    vector_similarity: float | None = None

    def with_overrides(self, **changes: Any) -> "SearchHit":
        """Return a copy with selected fields replaced — used by hybrid fusion."""
        return replace(self, **changes)


class Retriever(Protocol):
    """Provider-agnostic retrieval interface."""

    async def search(
        self,
        *,
        query: str,
        org_id: str,
        client: Client,
        k: int = 10,
        document_id: str | None = None,
    ) -> list[SearchHit]: ...


class VectorRetriever:
    """pgvector-backed retrieval. Caller passes a Supabase client whose
    auth scope matches the requesting user (so RLS fires)."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    async def search(
        self,
        *,
        query: str,
        org_id: str,
        client: Client,
        k: int = 10,
        document_id: str | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []

        vectors = await self.embedder.embed_texts([query], task_type="RETRIEVAL_QUERY")
        if not vectors:
            return []
        query_vec = vectors[0]

        params: dict = {
            "query_embedding": query_vec,
            "match_org_id": org_id,
            "match_count": k,
        }
        if document_id is not None:
            params["match_document_id"] = document_id

        try:
            response = await asyncio.to_thread(
                lambda: client.rpc("vector_search", params).execute()
            )
        except Exception as exc:
            log.error("vector_search RPC failed for org=%s: %s", org_id, exc)
            raise

        rows = response.data or []
        return [_row_to_hit(r) for r in rows]


def _row_to_hit(row: dict) -> SearchHit:
    sim = float(row["similarity"])
    return SearchHit(
        chunk_id=row["chunk_id"],
        content=row["content"],
        document_id=row["document_id"],
        document_name=row["document_name"],
        chunk_index=row["chunk_index"],
        page_number=row.get("page_number"),
        section_heading=row.get("section_heading"),
        similarity=sim,
        vector_similarity=sim,
    )


_default_retriever: VectorRetriever | None = None


def get_vector_retriever() -> VectorRetriever:
    global _default_retriever
    if _default_retriever is None:
        _default_retriever = VectorRetriever()
    return _default_retriever


async def vector_search(
    query: str,
    org_id: str,
    client: Client,
    *,
    k: int = 10,
    document_id: str | None = None,
) -> list[SearchHit]:
    """Convenience wrapper around the default retriever."""
    return await get_vector_retriever().search(
        query=query,
        org_id=org_id,
        client=client,
        k=k,
        document_id=document_id,
    )
