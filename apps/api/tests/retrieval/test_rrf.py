"""Pure unit tests for Reciprocal Rank Fusion.

No I/O, no mocks — just the fusion math. Any breakage here means the
ranking logic is broken, not a network or auth issue.
"""
import pytest

from app.services.retrieval.hybrid_search import reciprocal_rank_fusion, DEFAULT_RRF_K
from app.services.retrieval.vector_search import SearchHit


# ─── helpers ─────────────────────────────────────────────────────────────────

def hit(
    chunk_id: str,
    similarity: float = 0.5,
    snippet: str | None = None,
    vector_similarity: float | None = None,
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        content=f"content of {chunk_id}",
        document_id="doc-1",
        document_name="Test Doc",
        chunk_index=0,
        page_number=None,
        section_heading=None,
        similarity=similarity,
        snippet=snippet,
        vector_similarity=vector_similarity,
    )


def vector_hits(*ids: str) -> list[SearchHit]:
    return [hit(cid, similarity=1.0 - i * 0.05) for i, cid in enumerate(ids)]


def fts_hits(*ids: str) -> list[SearchHit]:
    return [hit(cid, similarity=0.9 - i * 0.05, snippet=f"<mark>{cid}</mark>") for i, cid in enumerate(ids)]


# ─── empty / degenerate inputs ────────────────────────────────────────────────

def test_both_empty():
    assert reciprocal_rank_fusion([], []) == []


def test_only_vector_hits_returned_when_fts_empty():
    v = vector_hits("a", "b", "c")
    result = reciprocal_rank_fusion(v, [], top_k=10)
    assert [h.chunk_id for h in result] == ["a", "b", "c"]


def test_only_fts_hits_returned_when_vector_empty():
    f = fts_hits("x", "y")
    result = reciprocal_rank_fusion([], f, top_k=10)
    assert [h.chunk_id for h in result] == ["x", "y"]


def test_top_k_limits_output():
    v = vector_hits(*[str(i) for i in range(20)])
    f = fts_hits(*[str(i) for i in range(20, 40)])
    result = reciprocal_rank_fusion(v, f, top_k=5)
    assert len(result) == 5


def test_top_k_zero_returns_empty():
    v = vector_hits("a", "b")
    f = fts_hits("c", "d")
    assert reciprocal_rank_fusion(v, f, top_k=0) == []


# ─── scoring correctness ──────────────────────────────────────────────────────

def test_chunk_in_both_branches_outscores_single_branch():
    # "shared" appears at rank 0 in both; "vector_only" only in vector at rank 1
    v = [hit("shared"), hit("vector_only")]
    f = [hit("shared")]
    result = reciprocal_rank_fusion(v, f, top_k=5)
    ids = [h.chunk_id for h in result]
    assert ids.index("shared") < ids.index("vector_only")


def test_overlap_rrf_score_is_sum_of_both_branches():
    # rank-0 in both branches → score = 1/(k+1) + 1/(k+1)
    k = DEFAULT_RRF_K
    expected_score = 2.0 / (k + 1)
    v = [hit("shared")]
    f = [hit("shared")]
    result = reciprocal_rank_fusion(v, f, top_k=1)
    assert result[0].rrf_score == pytest.approx(expected_score, rel=1e-9)


def test_vector_only_rrf_score():
    k = DEFAULT_RRF_K
    expected = 1.0 / (k + 1)
    result = reciprocal_rank_fusion([hit("a")], [], top_k=5)
    assert result[0].rrf_score == pytest.approx(expected, rel=1e-9)


def test_fts_only_rrf_score():
    k = DEFAULT_RRF_K
    expected = 1.0 / (k + 1)
    result = reciprocal_rank_fusion([], [hit("b")], top_k=5)
    assert result[0].rrf_score == pytest.approx(expected, rel=1e-9)


def test_higher_rank_has_higher_score():
    v = vector_hits("first", "second", "third")
    result = reciprocal_rank_fusion(v, [], top_k=3)
    scores = [h.rrf_score for h in result]
    assert scores == sorted(scores, reverse=True)


def test_custom_k_rrf_changes_scores():
    v = [hit("a"), hit("b")]
    low_k = reciprocal_rank_fusion(v, [], k_rrf=1, top_k=2)
    high_k = reciprocal_rank_fusion(v, [], k_rrf=1000, top_k=2)
    # Low k_rrf makes top-rank more dominant; scores should differ
    assert low_k[0].rrf_score != pytest.approx(high_k[0].rrf_score)


# ─── metadata fields ──────────────────────────────────────────────────────────

def test_match_sources_vector_only():
    result = reciprocal_rank_fusion([hit("a")], [], top_k=5)
    assert result[0].match_sources == ("vector",)


def test_match_sources_fts_only():
    result = reciprocal_rank_fusion([], [hit("a")], top_k=5)
    assert result[0].match_sources == ("fts",)


def test_match_sources_both_branches():
    result = reciprocal_rank_fusion([hit("a")], [hit("a")], top_k=5)
    assert result[0].match_sources == ("vector", "fts")


def test_vector_rank_set_correctly():
    v = vector_hits("a", "b", "c")
    result = reciprocal_rank_fusion(v, [], top_k=3)
    for expected_rank, h in enumerate(result):
        assert h.vector_rank == expected_rank


def test_fts_rank_none_for_vector_only_hit():
    result = reciprocal_rank_fusion([hit("a")], [hit("b")], top_k=5)
    a = next(h for h in result if h.chunk_id == "a")
    assert a.fts_rank is None


def test_vector_rank_none_for_fts_only_hit():
    result = reciprocal_rank_fusion([hit("a")], [hit("b")], top_k=5)
    b = next(h for h in result if h.chunk_id == "b")
    assert b.vector_rank is None


def test_fts_rank_set_on_overlap():
    v = [hit("shared")]
    f = [hit("other"), hit("shared")]  # "shared" is rank 1 in FTS
    result = reciprocal_rank_fusion(v, f, top_k=5)
    shared = next(h for h in result if h.chunk_id == "shared")
    assert shared.fts_rank == 1


# ─── snippet propagation ──────────────────────────────────────────────────────

def test_fts_snippet_carried_to_overlap_hit():
    # chunk "a" is in both; FTS provides a snippet
    v = [hit("a", snippet=None)]
    f = [hit("a", snippet="<mark>a</mark> text")]
    result = reciprocal_rank_fusion(v, f, top_k=5)
    assert result[0].snippet == "<mark>a</mark> text"


def test_existing_vector_snippet_not_overwritten():
    # vector already has a snippet (unusual but possible); FTS snippet ignored
    v = [hit("a", snippet="original")]
    f = [hit("a", snippet="from fts")]
    result = reciprocal_rank_fusion(v, f, top_k=5)
    assert result[0].snippet == "original"


def test_fts_only_hit_keeps_its_snippet():
    f = [hit("z", snippet="<mark>z</mark>")]
    result = reciprocal_rank_fusion([], f, top_k=5)
    assert result[0].snippet == "<mark>z</mark>"


# ─── determinism ──────────────────────────────────────────────────────────────

def test_output_is_deterministic():
    v = vector_hits("a", "b", "c", "d")
    f = fts_hits("c", "d", "e", "f")
    r1 = [h.chunk_id for h in reciprocal_rank_fusion(v, f, top_k=5)]
    r2 = [h.chunk_id for h in reciprocal_rank_fusion(v, f, top_k=5)]
    assert r1 == r2


def test_large_inputs_no_crash():
    v = vector_hits(*[f"v{i}" for i in range(500)])
    f = fts_hits(*[f"f{i}" for i in range(500)])
    result = reciprocal_rank_fusion(v, f, top_k=10)
    assert len(result) == 10
