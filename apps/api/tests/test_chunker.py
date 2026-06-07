"""Deterministic chunker tests."""
from __future__ import annotations

from app.services.ingestion.chunker import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    chunk_segments,
)
from app.services.ingestion.types import RawSegment


def test_short_segment_yields_single_chunk():
    seg = RawSegment(
        content="Short paragraph.",
        section_heading="Intro",
        page_number=1,
    )
    chunks = chunk_segments([seg])
    assert len(chunks) == 1
    assert chunks[0].section_heading == "Intro"
    assert chunks[0].page_number == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].token_count > 0


def test_long_segment_splits_under_budget():
    long_text = "This is a sentence. " * 800  # comfortably above 800 tokens
    seg = RawSegment(content=long_text, section_heading="Long", page_number=5)
    chunks = chunk_segments([seg])
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= CHUNK_SIZE_TOKENS + 50, "chunks must respect token budget"
        assert c.section_heading == "Long"
        assert c.page_number == 5
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_different_headings_become_different_groups():
    segments = [
        RawSegment(content="Body of A.", section_heading="A", page_number=1),
        RawSegment(content="Body of B.", section_heading="B", page_number=2),
    ]
    chunks = chunk_segments(segments)
    by_heading = {c.section_heading: c for c in chunks}
    assert "A" in by_heading and "B" in by_heading
    assert by_heading["A"].page_number == 1
    assert by_heading["B"].page_number == 2


def test_page_number_tracks_across_long_section():
    p1 = RawSegment(
        content="Page one content. " * 400, section_heading="X", page_number=1
    )
    p2 = RawSegment(
        content="Page two content. " * 400, section_heading="X", page_number=2
    )
    chunks = chunk_segments([p1, p2])
    pages = [c.page_number for c in chunks]
    assert 1 in pages and 2 in pages


def test_empty_input_returns_empty():
    assert chunk_segments([]) == []


def test_whitespace_only_yields_no_chunks():
    seg = RawSegment(content="   \n\n   ", section_heading="Blank")
    assert chunk_segments([seg]) == []


def test_overlap_is_configured():
    # Sanity check that the chunker module exports its tuning knobs.
    assert CHUNK_SIZE_TOKENS == 800
    assert CHUNK_OVERLAP_TOKENS == 150
