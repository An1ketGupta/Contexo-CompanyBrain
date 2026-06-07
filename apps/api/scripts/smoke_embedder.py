"""Live embedder test — calls Gemini once. Requires GEMINI_API_KEY in .env."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingestion import Chunk, embed_chunks


async def main() -> None:
    chunks = [
        Chunk(
            content="The company offers a 30-day refund policy on all standard plans.",
            chunk_index=0,
            token_count=18,
            page_number=1,
            section_heading="Refunds",
        ),
        Chunk(
            content="Engineering interviews are conducted in two rounds: technical and culture.",
            chunk_index=1,
            token_count=15,
            page_number=2,
            section_heading="Hiring",
        ),
    ]
    embedded = await embed_chunks(chunks)
    for ec in embedded:
        print(
            f"chunk {ec.chunk.chunk_index}: heading={ec.chunk.section_heading!r} "
            f"dims={len(ec.embedding)} sample={ec.embedding[:3]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
