"""Tests for the deterministic pieces of the retrieval module.

The SQL function itself needs a live database — `scripts/smoke_vector_search.py`
covers that end-to-end. Here we test the row-to-dataclass mapping.
"""
from __future__ import annotations

from app.services.retrieval import SearchHit
from app.services.retrieval.vector_search import _row_to_hit


def test_row_to_hit_full_payload():
    row = {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "content": "Body text.",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "document_name": "Handbook.pdf",
        "chunk_index": 7,
        "page_number": 3,
        "section_heading": "Refunds",
        "similarity": 0.82,
    }
    hit = _row_to_hit(row)
    assert isinstance(hit, SearchHit)
    assert hit.chunk_id == row["chunk_id"]
    assert hit.content == "Body text."
    assert hit.document_name == "Handbook.pdf"
    assert hit.chunk_index == 7
    assert hit.page_number == 3
    assert hit.section_heading == "Refunds"
    assert hit.similarity == 0.82


def test_row_to_hit_null_optionals():
    row = {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "content": "Body.",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "document_name": "notes.md",
        "chunk_index": 0,
        "page_number": None,
        "section_heading": None,
        "similarity": 0.91,
    }
    hit = _row_to_hit(row)
    assert hit.page_number is None
    assert hit.section_heading is None
    assert hit.similarity == 0.91


def test_row_to_hit_similarity_coerced_to_float():
    row = {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "content": "x",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "document_name": "x",
        "chunk_index": 0,
        "page_number": None,
        "section_heading": None,
        "similarity": "0.5",
    }
    hit = _row_to_hit(row)
    assert hit.similarity == 0.5
    assert isinstance(hit.similarity, float)


def test_search_hit_is_frozen():
    hit = SearchHit(
        chunk_id="a",
        content="b",
        document_id="c",
        document_name="d",
        chunk_index=0,
        page_number=None,
        section_heading=None,
        similarity=1.0,
    )
    try:
        hit.similarity = 0.5  # type: ignore[misc]
    except Exception:
        pass
    assert hit.similarity == 1.0
