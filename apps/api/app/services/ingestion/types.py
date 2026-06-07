"""Dataclasses for the ingestion pipeline.

Three stages, three shapes:
    RawSegment    parser output, before chunking
    Chunk         chunker output, ready for embedding/storage
    EmbeddedChunk chunk + its vector, ready for persistence
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RawSegment:
    """A paragraph-level unit extracted from a document."""

    content: str
    page_number: int | None = None
    section_heading: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    """A token-sized unit ready to be embedded and stored."""

    content: str
    chunk_index: int
    token_count: int
    page_number: int | None = None
    section_heading: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    embedding: list[float]
