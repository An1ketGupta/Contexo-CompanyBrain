"""Embedding fine-tune execution + Modal client wrapper (V5 #106 Phase 2).

Architecture:

  ┌──────────────┐ trigger ┌────────────────┐ POST JSONL ┌────────────┐
  │ Admin UI     │────────▶│ FastAPI:       │───────────▶│ Modal app  │
  │ /admin/embed │         │ POST /admin/   │            │ (separate  │
  └──────────────┘         │   embeddings/  │            │  deploy)   │
                           │   fine-tune    │            │            │
                           └────────┬───────┘            │  – train   │
                                    │ enqueue            │  – host    │
                                    ▼                    │            │
                           ┌────────────────┐  poll      │            │
                           │ Inngest:       │◀──────────▶│            │
                           │ embeddings/    │            │            │
                           │ fine-tune      │            │            │
                           └────────┬───────┘            └────────────┘
                                    │ on deploy
                                    ▼
                           ┌────────────────┐
                           │ Inngest:       │
                           │ embeddings/    │  re-embed every chunk in the
                           │ reembed-org    │  org through the FT model
                           └────────────────┘

Modal is a *separate deploy* from FastAPI — the Modal app file lives in
`modal_apps/embedding_finetune.py` at the repo root and is `modal deploy`-ed
once per environment. We talk to it over HTTP using the configured endpoint
+ token. If the env vars are empty, all training UI is gated and the Inngest
worker no-ops cleanly.

Why we don't auto-deploy: deploying a fine-tuned model means re-embedding
every chunk for the org. That's a non-trivial cost (10k chunks × 1 embed
call each) and can take minutes. We gate it behind an explicit admin click
+ enterprise plan check + an eval-improvement floor (hit@5 must improve).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.database import get_service_client
from app.observability import get_logger

log = get_logger(__name__)


# ── Modal HTTP client ───────────────────────────────────────────────────────


class ModalNotConfigured(Exception):
    """Raised when MODAL_FINETUNE_* env vars are missing. The admin UI gates on
    this so users see a clear "backend not configured" state instead of a
    cryptic HTTPS error."""


@dataclass(frozen=True)
class ModalSubmissionResult:
    job_id: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class ModalJobStatus:
    state: str  # 'pending' | 'training' | 'evaluating' | 'succeeded' | 'failed'
    fine_tuned_model_id: str | None
    eval_score_before: float | None
    eval_score_after: float | None
    error: str | None
    raw: dict[str, Any]


def _modal_url(suffix: str = "") -> str:
    settings = get_settings()
    base = (settings.modal_finetune_endpoint or "").rstrip("/")
    if not base:
        raise ModalNotConfigured("MODAL_FINETUNE_ENDPOINT is empty")
    if suffix and not suffix.startswith("/"):
        suffix = "/" + suffix
    return base + suffix


def _modal_status_url(job_id: str) -> str:
    settings = get_settings()
    base = (settings.modal_finetune_status_endpoint or "").rstrip("/")
    if not base:
        # Default convention: <endpoint>/status/<job_id>
        return _modal_url(f"/status/{job_id}")
    return f"{base.rstrip('/')}/{job_id}"


def _auth_headers() -> dict[str, str]:
    settings = get_settings()
    token = (settings.modal_finetune_token or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


async def submit_modal_job(
    *,
    org_id: str,
    base_model: str,
    training_pairs: list[dict[str, Any]],
) -> ModalSubmissionResult:
    """POST a training JSONL payload to Modal. Returns the job_id Modal assigns
    so we can poll. Times out at 60s on the connection; the actual job runs
    async on Modal."""
    if not training_pairs:
        raise ValueError("training_pairs is empty — refusing to submit")

    settings = get_settings()
    if not settings.modal_finetune_endpoint:
        raise ModalNotConfigured("MODAL_FINETUNE_ENDPOINT is empty")

    # Modal expects newline-delimited JSON. We post as a JSON body containing
    # the JSONL inline so the receiver can validate before kicking off the
    # training process (vs. having to spool the stream first).
    body = {
        "org_id": org_id,
        "base_model": base_model,
        "training_pairs": training_pairs,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
    ) as client:
        resp = await client.post(
            _modal_url(),
            headers={
                **_auth_headers(),
                "Content-Type": "application/json",
            },
            content=json.dumps(body),
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Modal submission failed: {resp.status_code} {resp.text[:300]}"
        )
    data = resp.json()
    job_id = data.get("job_id")
    if not job_id:
        raise RuntimeError(f"Modal returned no job_id: {data}")
    return ModalSubmissionResult(job_id=str(job_id), raw=data)


async def poll_modal_job(job_id: str) -> ModalJobStatus:
    """Single poll. Inngest's step.run handles the retry loop with backoff."""
    if not job_id:
        raise ValueError("job_id required")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        resp = await client.get(_modal_status_url(job_id), headers=_auth_headers())
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Modal status failed: {resp.status_code} {resp.text[:300]}"
        )
    data = resp.json()
    return ModalJobStatus(
        state=str(data.get("state") or "pending"),
        fine_tuned_model_id=data.get("fine_tuned_model_id"),
        eval_score_before=_to_float(data.get("eval_score_before")),
        eval_score_after=_to_float(data.get("eval_score_after")),
        error=data.get("error"),
        raw=data,
    )


def _to_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Training-data export ────────────────────────────────────────────────────


async def export_training_pairs_for_org(
    org_id: str, *, limit: int = 2000
) -> list[dict[str, Any]]:
    """Build the JSONL-ready payload for an org.

    Each row is a contrastive triple:
        { query, positive, negatives[] }

    where positive/negatives are CHUNK CONTENT (not ids). We join against the
    `chunks` table here to avoid asking Modal to do that. Tolerates stale
    chunk ids (skipped silently).
    """

    def _query() -> list[dict[str, Any]]:
        svc = get_service_client()
        pairs = (
            svc.table("embedding_training_pairs")
            .select(
                "id, query_text, positive_chunk_id, negative_chunk_ids, signal_type, created_at"
            )
            .eq("org_id", org_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )
        if not pairs:
            return []
        # Collect every chunk id referenced and bulk-load.
        all_ids: set[str] = set()
        for p in pairs:
            if p.get("positive_chunk_id"):
                all_ids.add(p["positive_chunk_id"])
            for n in p.get("negative_chunk_ids") or []:
                all_ids.add(n)
        chunks_res = (
            svc.table("chunks")
            .select("id, content")
            .in_("id", list(all_ids))
            .execute()
            .data
            or []
        )
        by_id = {c["id"]: (c.get("content") or "")[:1000] for c in chunks_res}

        out: list[dict[str, Any]] = []
        for p in pairs:
            pos = by_id.get(p.get("positive_chunk_id") or "")
            if not pos:
                continue
            negs = [
                by_id[n]
                for n in (p.get("negative_chunk_ids") or [])
                if n in by_id
            ]
            out.append(
                {
                    "query": (p.get("query_text") or "")[:500],
                    "positive": pos,
                    "negatives": negs,
                    "signal": p.get("signal_type"),
                }
            )
        return out

    return await asyncio.to_thread(_query)


# ── Job + model state helpers ───────────────────────────────────────────────


def update_job_state_sync(
    job_id: str,
    *,
    status: str | None = None,
    fine_tuned_model_id: str | None = None,
    external_job_id: str | None = None,
    eval_score_before: float | None = None,
    eval_score_after: float | None = None,
    error_message: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    training_pairs_count: int | None = None,
) -> None:
    svc = get_service_client()
    patch: dict[str, Any] = {}
    if status is not None:
        patch["status"] = status
    if fine_tuned_model_id is not None:
        patch["fine_tuned_model_id"] = fine_tuned_model_id
    if external_job_id is not None:
        patch["external_job_id"] = external_job_id
    if eval_score_before is not None:
        patch["eval_score_before"] = eval_score_before
    if eval_score_after is not None:
        patch["eval_score_after"] = eval_score_after
    if error_message is not None:
        patch["error_message"] = error_message[:2000]
    if started_at is not None:
        patch["started_at"] = started_at
    if completed_at is not None:
        patch["completed_at"] = completed_at
    if training_pairs_count is not None:
        patch["training_pairs_count"] = training_pairs_count
    if not patch:
        return
    svc.table("embedding_fine_tune_jobs").update(patch).eq("id", job_id).execute()


def deploy_org_embedding_model(org_id: str, model_id: str) -> None:
    """Stamp the deployed model id onto organizations.metadata so retrieval
    + future ingestion use it. Existing org.metadata is preserved; only the
    embedding-related keys are written/overwritten.

    The retrieval client reads `metadata->>embedding_model` on every search,
    cached for 60s in get_org_config. No restart required after deploy.
    """
    svc = get_service_client()
    from datetime import datetime, timezone

    org = (
        svc.table("organizations")
        .select("metadata")
        .eq("id", org_id)
        .maybe_single()
        .execute()
        .data
        or {}
    )
    metadata = dict(org.get("metadata") or {})
    metadata["embedding_model"] = model_id
    metadata["embedding_fine_tuned_at"] = datetime.now(timezone.utc).isoformat()
    svc.table("organizations").update({"metadata": metadata}).eq("id", org_id).execute()


def org_has_active_job(org_id: str) -> dict[str, Any] | None:
    """Return the currently-active job row for an org, if any. Used by the
    admin endpoint + the start trigger to prevent concurrent fine-tunes."""
    svc = get_service_client()
    res = (
        svc.table("embedding_fine_tune_jobs")
        .select("*")
        .eq("org_id", org_id)
        .in_(
            "status",
            ["pending", "collecting_data", "training", "evaluating", "reembedding"],
        )
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    return res[0] if res else None


__all__ = [
    "ModalJobStatus",
    "ModalNotConfigured",
    "ModalSubmissionResult",
    "deploy_org_embedding_model",
    "export_training_pairs_for_org",
    "org_has_active_job",
    "poll_modal_job",
    "submit_modal_job",
    "update_job_state_sync",
]
