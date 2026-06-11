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
        document_ids: list[str] | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        # If a tag scope resolved to zero docs there's nothing to retrieve —
        # save both RPC round-trips. We propagate this to the inner retrievers
        # too so they no-op consistently, but checking here keeps the gather
        # call from spinning up tasks for an inevitable empty result.
        if document_ids is not None and len(document_ids) == 0:
            return []

        vector_task = self.vector.search(
            query=query,
            org_id=org_id,
            client=client,
            k=self._branch_k,
            document_id=document_id,
            document_ids=document_ids,
        )
        fts_task = self.fts.search(
            query=query,
            org_id=org_id,
            client=client,
            k=self._branch_k,
            document_id=document_id,
            document_ids=document_ids,
        )

        # return_exceptions: a transient failure in one branch shouldn't
        # take down retrieval entirely; we degrade to the surviving branch.
        results = await asyncio.gather(vector_task, fts_task, return_exceptions=True)
        vector_hits = _unwrap(results[0], "vector")
        fts_hits = _unwrap(results[1], "fts")

        if not vector_hits and not fts_hits:
            return []
        if not fts_hits:
            fused = list(vector_hits[:k])
        elif not vector_hits:
            fused = list(fts_hits[:k])
        else:
            fused = reciprocal_rank_fusion(
                vector_hits, fts_hits, k_rrf=self._rrf_k, top_k=k
            )

        # Citation-count boost (Day 12 / #49). Additive on the RRF score so
        # we don't override the ranking — just tip the scales toward chunks
        # from documents the org has historically found useful. Cap at ~10%
        # of typical RRF top scores so it can't dominate the order.
        return await _apply_citation_boost(fused, org_id=org_id)


async def _apply_citation_boost(
    hits: list[SearchHit],
    *,
    org_id: str,
) -> list[SearchHit]:
    """Re-rank by RRF + a small additive prior derived from doc citation counts.

    Empty result, single-document org, or no citations recorded yet → no-op
    (the boost contribution is zero everywhere, so order is preserved).
    """
    if not hits:
        return hits
    doc_ids = list({h.document_id for h in hits if h.document_id})
    if not doc_ids:
        return hits

    # Local import keeps the retrieval module independent of analytics writes —
    # citation_tracker depends on database/langfuse modules we'd rather not pull
    # into every retrieval call site.
    from app.services.citation_tracker import fetch_document_citation_counts

    counts = await fetch_document_citation_counts(
        org_id=org_id, document_ids=doc_ids
    )
    if not counts or all(c == 0 for c in counts.values()):
        return hits

    max_count = max(counts.values()) or 1
    boosted: list[SearchHit] = []
    for h in hits:
        c = counts.get(h.document_id, 0)
        # 0..0.005 — RRF top hits sit around 0.03–0.05, so this nudges close
        # results without ever overtaking a clearly-better-matched chunk.
        boost = (c / max_count) * 0.005
        if boost > 0:
            boosted.append(h.with_overrides(similarity=h.similarity + boost))
        else:
            boosted.append(h)
    return sorted(boosted, key=lambda h: h.similarity, reverse=True)


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
    document_ids: list[str] | None = None,
) -> list[SearchHit]:
    """Convenience wrapper around the default hybrid retriever."""
    return await get_hybrid_retriever().search(
        query=query,
        org_id=org_id,
        client=client,
        k=k,
        document_id=document_id,
        document_ids=document_ids,
    )
