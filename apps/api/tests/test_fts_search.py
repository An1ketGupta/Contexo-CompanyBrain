"""Deterministic tests for the FTS module — SQL behavior is covered by
`scripts/smoke_hybrid_search.py` against a live DB."""
from __future__ import annotations

from app.services.retrieval import SearchHit
from app.services.retrieval.fts_search import _row_to_hit


def test_row_to_hit_with_snippet():
    row = {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "content": "Our Q4-2024 revenue target is $10M.",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "document_name": "FY24 Plan.pdf",
        "chunk_index": 2,
        "page_number": 1,
        "section_heading": "Targets",
        "similarity": 0.91,
        "snippet": "Our <mark>Q4-2024</mark> revenue target",
    }
    hit = _row_to_hit(row)
    assert isinstance(hit, SearchHit)
    assert hit.snippet == "Our <mark>Q4-2024</mark> revenue target"
    assert hit.similarity == 0.91
    assert hit.section_heading == "Targets"


def test_row_to_hit_without_snippet():
    row = {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "content": "x",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "document_name": "x.md",
        "chunk_index": 0,
        "page_number": None,
        "section_heading": None,
        "similarity": "0.42",
    }
    hit = _row_to_hit(row)
    assert hit.snippet is None
    assert hit.similarity == 0.42
    assert isinstance(hit.similarity, float)
