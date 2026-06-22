"""Embedding adapter — provider-agnostic interface with optional fallback.

Primary today is Google (`gemini-embedding-001` @ 768d).  When
HUGGINGFACE_API_KEY is also set, the primary is wrapped in a FallbackEmbedder
that switches to HuggingFace (`sentence-transformers/all-mpnet-base-v2`, also
768d) the moment Gemini returns a quota / 429 error.

Dev-only safety net: mixing providers means vectors from different models land
in the same pgvector(768) column even though they live in different semantic
spaces. Retrieval quality degrades for the docs embedded with the fallback;
acceptable while iterating, not acceptable for production. Standardize on one
provider before launch and re-embed.

For retrieval quality, we prepend the section_heading to each chunk's text
before embedding (the persisted `content` field is unchanged). This anchors
every chunk inside its document section, even chunks that fall mid-paragraph.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from contextvars import ContextVar
from typing import Any, Literal, Protocol

import httpx
from google import genai
from google.genai import types as genai_types
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
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


def _is_quota_error(exc: BaseException) -> bool:
    """Detect provider-side quota / rate-limit errors that won't recover by retrying."""
    msg = str(exc).lower()
    return (
        "429" in msg
        or "resource_exhausted" in msg
        or "quota" in msg
        or "rate limit" in msg
        or "rate-limit" in msg
        or "too many requests" in msg
    )


class GoogleEmbedder:
    """gemini-embedding-001 @ 768d via Matryoshka slice.

    Outputs are L2-normalized for cosine-similarity safety — Google only
    guarantees pre-normalized vectors at the native 3072 dimension.

    Retries on transient errors but NOT on quota / 429 — those mean the daily
    or per-minute cap is hit and waiting 30s of backoff won't fix it. Letting
    them surface immediately makes the FallbackEmbedder swap in quickly.
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
            retry=retry_if_exception(lambda e: not _is_quota_error(e)),
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


class HuggingFaceEmbedder:
    """HF Inference API — free fallback, 768d to match pgvector schema.

    Model: sentence-transformers/all-mpnet-base-v2 — picked because its 768d
    output drops straight into the existing embeddings column. Cold-start can
    add ~20s on the very first call (the API loads the model on demand); we
    pass `wait_for_model=true` so HF holds the connection until it's ready
    instead of returning a 503 we'd have to retry on.
    """

    output_dim = 768
    model = "sentence-transformers/all-mpnet-base-v2"
    # Smaller batches than Gemini — the free Inference API has tighter payload
    # and timeout limits, and a 503/timeout means re-embedding the whole batch.
    batch_size = 32

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("HUGGINGFACE_API_KEY is required for HuggingFaceEmbedder")
        # api-inference.huggingface.co was deprecated in 2025 (DNS returns no
        # A records — `getaddrinfo` fails). The serverless inference API now
        # lives behind router.huggingface.co under the /hf-inference/ prefix.
        self._url = (
            f"https://router.huggingface.co/hf-inference/models/{self.model}"
            f"/pipeline/feature-extraction"
        )
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def embed_texts(self, texts: list[str], task_type: TaskType) -> list[list[float]]:
        # HF sentence-transformers don't distinguish doc vs. query; task_type is ignored.
        del task_type
        if not texts:
            return []

        results: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            embeddings = await self._embed_batch_with_retry(batch)
            results.extend(embeddings)
        return results

    async def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential_jitter(initial=2, max=60),
            retry=retry_if_exception(lambda e: not _is_quota_error(e)),
            reraise=True,
        ):
            with attempt:
                return await self._embed_batch(batch)
        raise EmbeddingError("Unreachable: retry loop exited without returning")

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self._url,
                    headers=self._headers,
                    json={
                        "inputs": batch,
                        "options": {"wait_for_model": True},
                    },
                )
        except Exception as exc:
            log.warning("HF embedding call failed: %s: %s", type(exc).__name__, exc)
            raise

        if response.status_code == 429:
            raise EmbeddingError(f"HuggingFace 429 rate limit: {response.text[:200]}")
        if response.status_code != 200:
            raise EmbeddingError(
                f"HuggingFace API error {response.status_code}: {response.text[:200]}"
            )

        data: Any = response.json()
        if not isinstance(data, list):
            raise EmbeddingError(
                f"Unexpected HF response shape: {type(data).__name__}"
            )

        # HF returns [[float,...], ...] for a batch and [float,...] for a single
        # input. Normalize to the batch shape so the rest of the code is uniform.
        if data and isinstance(data[0], (int, float)):
            data = [data]

        if len(data) != len(batch):
            raise EmbeddingError(
                f"HF embedding count mismatch: sent {len(batch)}, got {len(data)}"
            )

        out: list[list[float]] = []
        for emb in data:
            if not isinstance(emb, list):
                raise EmbeddingError(
                    f"HF embedding not a list: {type(emb).__name__}"
                )
            if len(emb) != self.output_dim:
                raise EmbeddingError(
                    f"HF dim mismatch: got {len(emb)}, expected {self.output_dim}"
                )
            out.append(_l2_normalize([float(x) for x in emb]))
        return out


class FallbackEmbedder:
    """Wraps a primary embedder and switches to a fallback on quota errors.

    Why: lets ingestion keep running after Gemini's free-tier daily/per-minute
    cap is exhausted. Without this, the docs queued after the cap is hit fail
    permanently and need manual reprocessing.

    Constraint: both embedders must share the same `output_dim` because they
    write into the same pgvector column. Validated at construction.
    """

    def __init__(self, primary: Embedder, fallback: Embedder) -> None:
        if primary.output_dim != fallback.output_dim:
            raise ValueError(
                f"FallbackEmbedder dim mismatch: primary={primary.output_dim}, "
                f"fallback={fallback.output_dim}"
            )
        self._primary = primary
        self._fallback = fallback
        self.output_dim = primary.output_dim

    async def embed_texts(self, texts: list[str], task_type: TaskType) -> list[list[float]]:
        try:
            return await self._primary.embed_texts(texts, task_type)
        except Exception as exc:
            if _is_quota_error(exc):
                log.warning(
                    "Primary embedder hit quota (%s: %s); falling back to %s",
                    type(exc).__name__,
                    str(exc)[:200],
                    type(self._fallback).__name__,
                )
                return await self._fallback.embed_texts(texts, task_type)
            raise


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class ModalFineTunedEmbedder:
    """Calls a Modal-hosted fine-tuned sentence-transformers model.

    Used per-org when `organizations.metadata.embedding_model` starts with
    `modal:`. The endpoint URL + bearer token are global settings; the model
    id is encoded in the path (`<base>/embed/<model_id>`) so multiple orgs
    share one Modal app with isolated trained weights.

    Output dim is fixed at 768 to stay binary-compatible with the existing
    pgvector(768) column. Vectors are L2-normalized server-side; we re-
    normalize defensively in case a future Modal app skips it.
    """

    output_dim = 768

    def __init__(self, *, endpoint: str, token: str, model_id: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._model_id = model_id

    async def embed_texts(self, texts: list[str], task_type: TaskType) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self._endpoint}/embed/{self._model_id}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            resp = await client.post(
                url, headers=headers, json={"texts": texts, "task_type": task_type}
            )
        if resp.status_code >= 400:
            raise EmbeddingError(
                f"Modal embedder failed: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
        vectors = data.get("embeddings") or []
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Modal embedder count mismatch: sent {len(texts)}, got {len(vectors)}"
            )
        out: list[list[float]] = []
        for v in vectors:
            if not isinstance(v, list) or len(v) != self.output_dim:
                raise EmbeddingError(
                    f"Modal embedder bad shape: got dim {len(v) if isinstance(v, list) else type(v).__name__}"
                )
            out.append(_l2_normalize([float(x) for x in v]))
        return out


def _build_primary(provider: str, settings) -> Embedder:
    if provider == "google":
        return GoogleEmbedder(api_key=settings.gemini_api_key)
    if provider == "huggingface":
        return HuggingFaceEmbedder(api_key=settings.huggingface_api_key)
    raise ValueError(
        f"Unsupported embedding provider: {provider!r}. "
        f"Set EMBEDDING_PROVIDER to 'google' or 'huggingface'."
    )


def get_embedder() -> Embedder:
    """Return the embedder, optionally wrapped with a quota-aware fallback.

    V5 #106: when a current-org context is bound via `bind_org_for_embedding`
    and that org has deployed a fine-tuned model, returns a
    `ModalFineTunedEmbedder` pointing at that model instead of the default.
    Falls through to the standard logic when no org is bound (background
    jobs, etc.) or the org hasn't fine-tuned.

    Fallback fires when:
      * primary is `google`, and
      * HUGGINGFACE_API_KEY is configured.

    Setting EMBEDDING_PROVIDER=huggingface bypasses fallback entirely (no point
    falling back to the same provider).
    """
    org_embedder = _get_org_specific_embedder()
    if org_embedder is not None:
        return org_embedder

    settings = get_settings()
    provider = settings.embedding_provider.lower()
    primary = _build_primary(provider, settings)

    if provider == "google" and settings.huggingface_api_key:
        fallback = HuggingFaceEmbedder(api_key=settings.huggingface_api_key)
        log.debug("Embedder: GoogleEmbedder with HuggingFaceEmbedder fallback")
        return FallbackEmbedder(primary=primary, fallback=fallback)

    return primary


# ── Per-org fine-tuned embedder selection ──────────────────────────────────


_org_embedding_context: ContextVar[str | None] = ContextVar(
    "embedding_org_context", default=None
)

# Tiny TTL cache so we don't hit the DB for every search in a busy chat.
# Same 60s window the org_config cache uses — keeps the two coherent.
_org_model_cache: dict[str, tuple[float, str | None]] = {}
_ORG_MODEL_TTL_SECONDS = 60.0


def bind_org_for_embedding(org_id: str | None) -> Any:
    """Set the org id for the current async task. Returns the previous token so
    callers can restore in a finally block. No-op for falsy values."""
    if not org_id:
        return None
    return _org_embedding_context.set(org_id)


def reset_org_embedding(token: Any) -> None:
    if token is None:
        return
    try:
        _org_embedding_context.reset(token)
    except (LookupError, ValueError):
        pass


def _lookup_org_model(org_id: str) -> str | None:
    """Read organizations.metadata.embedding_model with a 60s TTL cache.
    Returns None when the org has no fine-tuned model deployed."""
    cached = _org_model_cache.get(org_id)
    now = time.monotonic()
    if cached is not None and (now - cached[0]) < _ORG_MODEL_TTL_SECONDS:
        return cached[1]

    from app.database import get_service_client

    try:
        res = (
            get_service_client()
            .table("organizations")
            .select("metadata")
            .eq("id", org_id)
            .maybe_single()
            .execute()
        )
        metadata = (getattr(res, "data", None) or {}).get("metadata") or {}
        model = metadata.get("embedding_model") if isinstance(metadata, dict) else None
        model_value = str(model) if model else None
    except Exception as exc:
        log.debug("org_embedding_lookup_failed: %s", exc)
        model_value = None

    _org_model_cache[org_id] = (now, model_value)
    return model_value


def _get_org_specific_embedder() -> Embedder | None:
    """Build a Modal-backed embedder for the bound org if it has a deployed
    fine-tuned model. Returns None otherwise."""
    org_id = _org_embedding_context.get()
    if not org_id:
        return None
    model = _lookup_org_model(org_id)
    if not model or not model.startswith("modal:"):
        return None
    settings = get_settings()
    if not settings.modal_finetune_endpoint:
        # Config drift — model was deployed but the inference endpoint is
        # missing from the env. Fall back to the default embedder so
        # retrieval still works (vectors will be in the wrong space but a
        # degraded answer beats a 500).
        log.warning(
            "org_finetuned_model_skipped_no_endpoint",
            org_id=org_id,
            model=model,
        )
        return None
    return ModalFineTunedEmbedder(
        endpoint=settings.modal_finetune_endpoint,
        token=settings.modal_finetune_token,
        model_id=model.removeprefix("modal:"),
    )


def invalidate_org_embedding_cache(org_id: str | None = None) -> None:
    """Drop the cached org → model mapping. Called after a successful
    deploy so the next search picks up the FT model immediately."""
    if org_id is None:
        _org_model_cache.clear()
    else:
        _org_model_cache.pop(org_id, None)


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
