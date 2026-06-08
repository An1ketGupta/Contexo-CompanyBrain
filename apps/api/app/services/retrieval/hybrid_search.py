"""Hybrid retrieval — vector + FTS fused via Reciprocal Rank Fusion.

Why hybrid:
    Vector search captures semantics ("paid time off" ≈ "vacation policy");
    FTS captures rare proper nouns / codes that embeddings smooth over
    ("Q4-2024", "SKU-A77", a customer's name). Combining them is the
    cheapest meaningful retrieval quality win available — no reranker
    model, no extra inference cost.

Why RRF (not score fusion):
    The two branches produce scores on incompatible scales — cosine ∈ [0, 1]
    vs ts_rank_cd (unbounded above, sensitive to corpus stats). RRF discards
    raw scores and fuses *ranks* only, which is robust, parameter-light, and
    well-understood. We use the standard k=60 from Cormack et al. (2009).

Behaviour:
    • Vector and FTS run in parallel via asyncio.gather (independent IO).
    • Each branch contributes its top `branch_k` hits (default 20).
    • Final result is top `k` by fused RRF score (default 10).
    • If one branch returns nothing, we transparently return the other —
      no fake ranks, no synthesized scores.
    • Each returned SearchHit carries `match_sources`, `vector_rank`,
      `fts_rank`, `rrf_score` for observability and the citation UI.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from supabase import Client

from .fts_search import FTSRetriever, get_fts_retriever
from .vector_search import (
    Retriever,
    SearchHit,
    VectorRetriever,
    get_vector_retriever,
)

log = logging.getLogger(__name__)

# RRF constant from Cormack/Clarke/Buettcher 2009. k=60 dampens the influence
# of the very top of each list so that agreement between branches dominates.
DEFAULT_RRF_K = 60
DEFAULT_BRANCH_K = 20
DEFAULT_FINAL_K = 10


def reciprocal_rank_fusion(
    vector_hits: Sequence[SearchHit],
    fts_hits: Sequence[SearchHit],
    *,
    k_rrf: int = DEFAULT_RRF_K,
    top_k: int = DEFAULT_FINAL_K,
) -> list[SearchHit]:
    """Fuse two ranked lists by Reciprocal Rank Fusion.

    Each chunk's fused score is the sum of 1/(k_rrf + rank) across the
    branches it appears in. Chunks present in both branches naturally float
    to the top — that's the whole point.
    """
    # chunk_id → {hit, vector_rank, fts_rank, score}
    accumulator: dict[str, dict] = {}

    for rank, hit in enumerate(vector_hits):
        accumulator[hit.chunk_id] = {
            "hit": hit,
            "vector_rank": rank,
            "fts_rank": None,
            "score": 1.0 / (k_rrf + rank + 1),
        }

    for rank, hit in enumerate(fts_hits):
        existing = accumulator.get(hit.chunk_id)
        if existing is None:
            accumulator[hit.chunk_id] = {
                "hit": hit,
                "vector_rank": None,
                "fts_rank": rank,
                "score": 1.0 / (k_rrf + rank + 1),
            }
        else:
            # Same chunk surfaced by both branches. Keep the vector-branch
            # SearchHit as the canonical (its `similarity` is cosine sim,
            # more useful than ts_rank for thresholding) but record FTS
            # provenance and merge the snippet if FTS produced one.
            existing["fts_rank"] = rank
            existing["score"] += 1.0 / (k_rrf + rank + 1)
            if existing["hit"].snippet is None and hit.snippet is not None:
                existing["hit"] = existing["hit"].with_overrides(snippet=hit.snippet)

    fused: list[SearchHit] = []
    items = sorted(accumulator.values(), key=lambda x: x["score"], reverse=True)
    for item in items[:top_k]:
        sources: list[str] = []
        if item["vector_rank"] is not None:
            sources.append("vector")
        if item["fts_rank"] is not None:
            sources.append("fts")
        fused.append(
            item["hit"].with_overrides(
                similarity=item["score"],
                match_sources=tuple(sources),
                vector_rank=item["vector_rank"],
                fts_rank=item["fts_rank"],
                rrf_score=item["score"],
            )
        )
    return fused


class HybridRetriever(Retriever):
    """Vector + FTS fusion. Implements the same `Retriever` protocol as
    its constituents, so callers (the LLM tool layer, the debug endpoint)
    don't know or care which strategy is in use."""

    def __init__(
        self,
        vector: VectorRetriever | None = None,
        fts: FTSRetriever | None = None,
        *,
        branch_k: int = DEFAULT_BRANCH_K,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._vector = vector
        self._fts = fts
        self._branch_k = branch_k
        self._rrf_k = rrf_k

    @property
    def vector(self) -> VectorRetriever:
        if self._vector is None:
            self._vector = get_vector_retriever()
        return self._vector

    @property
    def fts(self) -> FTSRetriever:
        if self._fts is None:
            self._fts = get_fts_retriever()
        return self._fts

    async def search(
        self,
        *,
        query: str,
        org_id: str,
        client: Client,
        k: int = DEFAULT_FINAL_K,
        document_id: str | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []

        vector_task = self.vector.search(
            query=query,
            org_id=org_id,
            client=client,
            k=self._branch_k,
            document_id=document_id,
        )
        fts_task = self.fts.search(
            query=query,
            org_id=org_id,
            client=client,
            k=self._branch_k,
            document_id=document_id,
        )

        # return_exceptions: a transient failure in one branch shouldn't
        # take down retrieval entirely; we degrade to the surviving branch.
        results = await asyncio.gather(vector_task, fts_task, return_exceptions=True)
        vector_hits = _unwrap(results[0], "vector")
        fts_hits = _unwrap(results[1], "fts")

        if not vector_hits and not fts_hits:
            return []
        if not fts_hits:
            return list(vector_hits[:k])
        if not vector_hits:
            return list(fts_hits[:k])

        return reciprocal_rank_fusion(
            vector_hits, fts_hits, k_rrf=self._rrf_k, top_k=k
        )


def _unwrap(result, branch: str) -> list[SearchHit]:
    if isinstance(result, BaseException):
        log.warning("hybrid_search: %s branch failed: %s", branch, result)
        return []
    return list(result)


_default_hybrid: HybridRetriever | None = None


def get_hybrid_retriever() -> HybridRetriever:
    global _default_hybrid
    if _default_hybrid is None:
        _default_hybrid = HybridRetriever()
    return _default_hybrid


async def hybrid_search(
    query: str,
    org_id: str,
    client: Client,
    *,
    k: int = DEFAULT_FINAL_K,
    document_id: str | None = None,
) -> list[SearchHit]:
    """Convenience wrapper around the default hybrid retriever."""
    return await get_hybrid_retriever().search(
        query=query,
        org_id=org_id,
        client=client,
        k=k,
        document_id=document_id,
    )
