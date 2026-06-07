"""Quick smoke test for the ingestion package — pure-Python pieces only."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingestion import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    RawSegment,
    chunk_segments,
    parse_document,
)


def main() -> None:
    print("imports OK")
    print(f"chunk size: {CHUNK_SIZE_TOKENS} tokens, overlap: {CHUNK_OVERLAP_TOKENS}")

    segments = [
        RawSegment(
            content="Intro paragraph.",
            section_heading="Introduction",
            page_number=1,
        ),
        RawSegment(
            content=(
                "More intro text here, padded a bit longer so the splitter has "
                "something real to look at and the tokenizer reports a non-trivial count."
            ),
            section_heading="Introduction",
            page_number=1,
        ),
        RawSegment(
            content="Different section now. " * 200,
            section_heading="Policies",
            page_number=2,
        ),
    ]
    chunks = chunk_segments(segments)
    print(f"chunked {len(segments)} segments -> {len(chunks)} chunks")
    for c in chunks[:3]:
        print(
            f"  chunk {c.chunk_index}: page={c.page_number} "
            f"heading={c.section_heading!r} tokens={c.token_count}"
        )

    txt_bytes = b"First paragraph.\n\nSecond paragraph with more words.\n\nThird."
    segs = parse_document(txt_bytes, "txt")
    print(f"parsed {len(segs)} txt segments")

    md_bytes = (
        b"# Title\n\nIntro line.\n\n## Section A\n\nBody of A.\n\n"
        b"## Section B\n\nBody of B with stuff."
    )
    segs = parse_document(md_bytes, "md")
    headings = sorted({s.section_heading or "<none>" for s in segs})
    print(f"parsed {len(segs)} md segments, headings: {headings}")


if __name__ == "__main__":
    main()
