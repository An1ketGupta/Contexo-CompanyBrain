"""Embedding adapter — provider-agnostic interface, Google as the only impl today.

Swap providers via EMBEDDING_PROVIDER without touching pipeline code.

For retrieval quality, we prepend the section_heading to each chunk's text before
embedding (the persisted `content` field is unchanged). This anchors every chunk
inside its document section, even chunks that fall mid-paragraph.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Literal, Protocol

from google import genai
from google.genai import types as genai_types
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings

from .types import Chunk, EmbeddedChunk

log = logging.getLogger(__name__)

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


class EmbeddingError(Exception):
    """Raised when an embedding call fails after all retries."""


class Embedder(Protocol):
    output_dim: int

    async def embed_texts(self, texts: list[str], task_type: TaskType) -> list[list[float]]: ...


class GoogleEmbedder:
    """gemini-embedding-001 @ 768d via Matryoshka slice.

    Outputs are L2-normalized for cosine-similarity safety — Google only
    guarantees pre-normalized vectors at the native 3072 dimension.
    """

    output_dim = 768
    model = "gemini-embedding-001"
    batch_size = 100  # Gemini accepts up to 100 inputs per call

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for GoogleEmbedder")
        self._client = genai.Client(api_key=api_key)

    async def embed_texts(self, texts: list[str], task_type: TaskType) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            embeddings = await self._embed_batch_with_retry(batch, task_type)
            results.extend(embeddings)
        return results

    async def _embed_batch_with_retry(
        self, batch: list[str], task_type: TaskType
    ) -> list[list[float]]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential_jitter(initial=1, max=30),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        ):
            with attempt:
                return await self._embed_batch(batch, task_type)
        raise EmbeddingError("Unreachable: retry loop exited without returning")

    async def _embed_batch(self, batch: list[str], task_type: TaskType) -> list[list[float]]:
        try:
            response = await asyncio.to_thread(
                lambda: self._client.models.embed_content(
                    model=self.model,
                    contents=batch,
                    config=genai_types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.output_dim,
                    ),
                )
            )
        except Exception as exc:
            log.warning("Gemini embedding call failed: %s: %s", type(exc).__name__, exc)
            raise

        embeddings = getattr(response, "embeddings", None)
        if embeddings is None:
            raise EmbeddingError(f"Unexpected response shape from Gemini: {response!r}")

        if len(embeddings) != len(batch):
            raise EmbeddingError(
                f"Embedding count mismatch: sent {len(batch)} texts, got {len(embeddings)}"
            )

        out: list[list[float]] = []
        for emb in embeddings:
            values = getattr(emb, "values", None)
            if values is None:
                raise EmbeddingError(f"Embedding missing .values: {emb!r}")
            if len(values) != self.output_dim:
                raise EmbeddingError(
                    f"Unexpected vector dimension {len(values)} (expected {self.output_dim})"
                )
            out.append(_l2_normalize(list(values)))
        return out


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def get_embedder() -> Embedder:
    settings = get_settings()
    provider = settings.embedding_provider.lower()
    if provider == "google":
        return GoogleEmbedder(api_key=settings.gemini_api_key)
    raise ValueError(
        f"Unsupported embedding provider: {provider!r}. Set EMBEDDING_PROVIDER=google."
    )


def _augment_for_embedding(chunk: Chunk) -> str:
    """Prepend section heading so each chunk carries its section context into the vector."""
    if chunk.section_heading:
        return f"{chunk.section_heading}\n\n{chunk.content}"
    return chunk.content


async def embed_chunks(chunks: list[Chunk], embedder: Embedder | None = None) -> list[EmbeddedChunk]:
    if not chunks:
        return []
    embedder = embedder or get_embedder()
    texts = [_augment_for_embedding(c) for c in chunks]
    vectors = await embedder.embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")
    return [EmbeddedChunk(chunk=c, embedding=v) for c, v in zip(chunks, vectors, strict=True)]
