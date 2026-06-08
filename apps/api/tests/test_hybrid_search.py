"""Unit tests for Reciprocal Rank Fusion — pure logic, no DB."""
from __future__ import annotations

import pytest

from app.services.retrieval import SearchHit, reciprocal_rank_fusion


def _hit(chunk_id: str, *, similarity: float = 0.0, snippet: str | None = None) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        content=f"content of {chunk_id}",
        document_id="doc-1",
        document_name="doc-1.pdf",
        chunk_index=0,
        page_number=None,
        section_heading=None,
        similarity=similarity,
        snippet=snippet,
    )


def test_rrf_chunk_in_both_branches_floats_to_top():
    """A chunk present in both ranked lists should beat a chunk in only one."""
    a = _hit("a", similarity=0.95)  # rank 0 in vector
    b = _hit("b", similarity=0.90)  # rank 1 in vector, rank 0 in FTS
    c = _hit("c", similarity=0.10)  # rank 0 in FTS only (after b)

    vector_hits = [a, b]
    fts_hits = [b, c]

    fused = reciprocal_rank_fusion(vector_hits, fts_hits, k_rrf=60, top_k=10)
    fused_ids = [h.chunk_id for h in fused]

    assert fused_ids[0] == "b", "chunk in both branches must rank first"
    assert set(fused_ids) == {"a", "b", "c"}


def test_rrf_records_branch_provenance():
    a = _hit("a")
    b = _hit("b")
    fused = reciprocal_rank_fusion([a], [b], k_rrf=60, top_k=10)
    by_id = {h.chunk_id: h for h in fused}

    assert by_id["a"].match_sources == ("vector",)
    assert by_id["a"].vector_rank == 0
    assert by_id["a"].fts_rank is None
    assert by_id["b"].match_sources == ("fts",)
    assert by_id["b"].fts_rank == 0
    assert by_id["b"].vector_rank is None


def test_rrf_records_both_sources_when_overlapping():
    a = _hit("a")
    fused = reciprocal_rank_fusion([a], [a], k_rrf=60, top_k=10)
    assert len(fused) == 1
    assert set(fused[0].match_sources) == {"vector", "fts"}
    assert fused[0].vector_rank == 0
    assert fused[0].fts_rank == 0
    # rrf_score for rank-0 in both branches = 2 * 1/(60+1)
    assert fused[0].rrf_score == pytest.approx(2.0 / 61.0)
    assert fused[0].similarity == pytest.approx(2.0 / 61.0)


def test_rrf_score_decreases_with_rank():
    """The standard RRF property: rank-0 must score higher than rank-9 in the same branch."""
    hits = [_hit(f"v{i}") for i in range(10)]
    fused = reciprocal_rank_fusion(hits, [], k_rrf=60, top_k=10)
    scores = [h.rrf_score for h in fused]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]


def test_rrf_top_k_truncates():
    vector_hits = [_hit(f"v{i}") for i in range(15)]
    fused = reciprocal_rank_fusion(vector_hits, [], k_rrf=60, top_k=5)
    assert len(fused) == 5


def test_rrf_empty_inputs():
    assert reciprocal_rank_fusion([], [], k_rrf=60, top_k=10) == []


def test_rrf_one_empty_branch_returns_the_other():
    a = _hit("a")
    b = _hit("b")
    fused = reciprocal_rank_fusion([a, b], [], k_rrf=60, top_k=10)
    assert [h.chunk_id for h in fused] == ["a", "b"]
    assert fused[0].match_sources == ("vector",)


def test_rrf_merges_snippet_from_fts_into_vector_hit():
    """When a chunk surfaces in both branches, the canonical hit is the vector
    one (cosine sim is more useful than ts_rank for thresholding), but we
    should carry the FTS snippet across so the UI can highlight matches."""
    v = _hit("a", similarity=0.88, snippet=None)
    f = _hit("a", similarity=0.42, snippet="match <mark>here</mark>")
    fused = reciprocal_rank_fusion([v], [f], k_rrf=60, top_k=10)
    assert len(fused) == 1
    assert fused[0].snippet == "match <mark>here</mark>"
    # Underlying canonical hit is the vector hit, but similarity is replaced
    # with the fused RRF score.
    assert fused[0].rrf_score == pytest.approx(2.0 / 61.0)


def test_rrf_does_not_overwrite_existing_snippet():
    v = _hit("a", snippet="existing snippet")
    f = _hit("a", snippet="fts snippet")
    fused = reciprocal_rank_fusion([v], [f], k_rrf=60, top_k=10)
    assert fused[0].snippet == "existing snippet"
