"""Tests for SearchHit dataclass semantics.

Frozen dataclass with Optional fields and a with_overrides() helper — the
tests verify the invariants that the rest of the retrieval stack depends on.
"""
import pytest

from app.services.retrieval.vector_search import SearchHit


def base_hit(**overrides) -> SearchHit:
    defaults = dict(
        chunk_id="chunk-1",
        content="Employee vacation policy allows 20 days.",
        document_id="doc-1",
        document_name="HR Handbook",
        chunk_index=3,
        page_number=7,
        section_heading="Leave Policy",
        similarity=0.82,
    )
    return SearchHit(**{**defaults, **overrides})


# ─── defaults ────────────────────────────────────────────────────────────────

def test_optional_fields_default_to_none():
    h = base_hit()
    assert h.snippet is None
    assert h.vector_rank is None
    assert h.fts_rank is None
    assert h.rrf_score is None
    assert h.vector_similarity is None


def test_match_sources_defaults_to_empty_tuple():
    h = base_hit()
    assert h.match_sources == ()
    assert isinstance(h.match_sources, tuple)


def test_all_required_fields_set():
    h = base_hit()
    assert h.chunk_id == "chunk-1"
    assert h.document_name == "HR Handbook"
    assert h.similarity == pytest.approx(0.82)


# ─── immutability ─────────────────────────────────────────────────────────────

def test_frozen_dataclass_rejects_mutation():
    h = base_hit()
    with pytest.raises((AttributeError, TypeError)):
        h.similarity = 0.99  # type: ignore[misc]


# ─── with_overrides ───────────────────────────────────────────────────────────

def test_with_overrides_returns_new_instance():
    h = base_hit()
    h2 = h.with_overrides(similarity=0.95)
    assert h is not h2


def test_with_overrides_does_not_mutate_original():
    h = base_hit(similarity=0.5)
    h.with_overrides(similarity=0.99)
    assert h.similarity == pytest.approx(0.5)


def test_with_overrides_changes_only_specified_field():
    h = base_hit(similarity=0.5, chunk_index=3)
    h2 = h.with_overrides(similarity=0.9)
    assert h2.chunk_index == 3
    assert h2.similarity == pytest.approx(0.9)


def test_with_overrides_can_set_multiple_fields():
    h = base_hit()
    h2 = h.with_overrides(
        similarity=0.03,
        match_sources=("vector", "fts"),
        vector_rank=0,
        fts_rank=2,
        rrf_score=0.03,
    )
    assert h2.match_sources == ("vector", "fts")
    assert h2.vector_rank == 0
    assert h2.fts_rank == 2


def test_with_overrides_snippet_propagation():
    h = base_hit(snippet=None)
    h2 = h.with_overrides(snippet="<mark>vacation</mark> policy")
    assert h2.snippet == "<mark>vacation</mark> policy"
    assert h.snippet is None  # original untouched


# ─── vector_similarity independence ──────────────────────────────────────────

def test_vector_similarity_and_similarity_can_differ():
    # After RRF fusion, similarity becomes the fused score while
    # vector_similarity keeps the original cosine value.
    h = base_hit(similarity=0.03, vector_similarity=0.91)
    assert h.similarity == pytest.approx(0.03)
    assert h.vector_similarity == pytest.approx(0.91)


def test_with_overrides_preserves_vector_similarity():
    h = base_hit(similarity=0.9, vector_similarity=0.9)
    h2 = h.with_overrides(similarity=0.03)  # RRF fusion step
    assert h2.vector_similarity == pytest.approx(0.9)
