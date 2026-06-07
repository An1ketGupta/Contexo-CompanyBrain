"""Token-aware chunking.

We split per-section so every chunk inherits its section_heading, and we keep a
char-offset → page_number map so each chunk reports the page it started on.

Length is measured with tiktoken cl100k_base. It isn't Gemini's exact tokenizer,
but it's a good monotone proxy and runs locally without an API call.
"""
from __future__ import annotations

import logging
from typing import TypeVar

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .types import Chunk, RawSegment

log = logging.getLogger(__name__)

CHUNK_SIZE_TOKENS = 800
CHUNK_OVERLAP_TOKENS = 150

_SEPARATORS = [
    "\n## ",      # markdown h2
    "\n# ",       # markdown h1
    "\n\n",       # paragraph break
    "\n",         # line break
    ". ",         # sentence
    "? ",
    "! ",
    "; ",
    ", ",
    " ",
    "",
]

_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def _token_len(text: str) -> int:
    return len(_TOKENIZER.encode(text, disallowed_special=()))


_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE_TOKENS,
    chunk_overlap=CHUNK_OVERLAP_TOKENS,
    length_function=_token_len,
    separators=_SEPARATORS,
    keep_separator=False,
)


T = TypeVar("T")


def chunk_segments(segments: list[RawSegment]) -> list[Chunk]:
    """Group segments by section_heading, then split each group token-aware."""
    out: list[Chunk] = []
    chunk_index = 0

    for group, heading in _group_by_heading(segments):
        combined, page_offsets = _join_with_page_map(group)
        if not combined.strip():
            continue

        splits = _splitter.split_text(combined)
        char_cursor = 0
        for split in splits:
            stripped = split.strip()
            if not stripped:
                continue

            pos = combined.find(stripped, char_cursor)
            if pos < 0:
                pos = char_cursor
            char_cursor = pos + len(stripped)

            out.append(
                Chunk(
                    content=stripped,
                    chunk_index=chunk_index,
                    token_count=_token_len(stripped),
                    page_number=_lookup_offset(pos, page_offsets),
                    section_heading=heading,
                )
            )
            chunk_index += 1

    return out


def _group_by_heading(
    segments: list[RawSegment],
) -> list[tuple[list[RawSegment], str | None]]:
    """Adjacent segments that share a section_heading get grouped together."""
    groups: list[tuple[list[RawSegment], str | None]] = []
    if not segments:
        return groups

    current: list[RawSegment] = [segments[0]]
    current_heading = segments[0].section_heading

    for seg in segments[1:]:
        if seg.section_heading == current_heading:
            current.append(seg)
        else:
            groups.append((current, current_heading))
            current = [seg]
            current_heading = seg.section_heading

    groups.append((current, current_heading))
    return groups


def _join_with_page_map(group: list[RawSegment]) -> tuple[str, list[tuple[int, int | None]]]:
    """Join group text with `\\n\\n` separators, tracking where each segment starts."""
    parts: list[str] = []
    page_offsets: list[tuple[int, int | None]] = []
    cursor = 0
    for i, seg in enumerate(group):
        page_offsets.append((cursor, seg.page_number))
        parts.append(seg.content)
        cursor += len(seg.content)
        if i < len(group) - 1:
            parts.append("\n\n")
            cursor += 2
    return "".join(parts), page_offsets


def _lookup_offset(pos: int, offsets: list[tuple[int, T]]) -> T | None:
    """Return the most recent offset's value <= pos."""
    result: T | None = None
    for off, val in offsets:
        if off <= pos:
            result = val
        else:
            break
    return result
