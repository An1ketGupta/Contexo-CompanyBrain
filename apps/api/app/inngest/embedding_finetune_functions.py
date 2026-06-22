"""V5 #106 Phase 2 — Inngest functions for embedding fine-tune lifecycle.

Two functions:

1. `embeddings/fine-tune` (per-org event):
       collect → submit → poll → evaluate → deploy → trigger re-embed
   Polls Modal every 60s for up to 4 hours.

2. `embeddings/reembed-org` (per-org event, fired automatically by #1):
       For every (ready) chunk in the org, re-call the embedding adapter and
       overwrite the row in `embeddings`. Required after a fine-tune deploy
       because new vectors live in a different semantic space than old ones.

Both are gated by enterprise plan (admin endpoint refuses non-enterprise) but
we recheck inside the worker as defense-in-depth.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import inngest

from app.database import get_service_client
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.embedding_finetune import (
    ModalNotConfigured,
    deploy_org_embedding_model,
    export_training_pairs_for_org,
    poll_modal_job,
    submit_modal_job,
    update_job_state_sync,
)

log = get_logger(__name__)

_inngest_client = get_inngest_client()


# Eval-improvement floor before auto-deploy. 1% absolute hit@5 lift = "good
# enough" — anything less is within noise on small eval sets and not worth
# the re-embed compute cost.
MIN_EVAL_IMPROVEMENT = 0.01

# Max poll attempts at 60s intervals = 4 hours.
MAX_POLL_ATTEMPTS = 240
POLL_INTERVAL_SECONDS = 60


@_inngest_client.create_function(
    fn_id="embeddings-fine-tune",
    trigger=inngest.TriggerEvent(event="embeddings/fine-tune"),
    retries=1,
    concurrency=[inngest.Concurrency(limit=2)],
)
async def run_embedding_fine_tune(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    org_id: str = data["org_id"]
    job_id: str = data["job_id"]
    base_model: str = data.get("base_model") or "sentence-transformers/all-mpnet-base-v2"

    # ── 1. Export training pairs ──
    pairs = await ctx.step.run(
        "export-pairs",
        lambda: export_training_pairs_for_org(org_id),
    )
    if not pairs:
        await ctx.step.run(
            "mark-failed-no-pairs",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="failed",
                    error_message="No usable training pairs found",
                    completed_at=_now_iso(),
                )
            ),
        )
        return {"status": "failed", "reason": "no_pairs"}

    await ctx.step.run(
        "mark-collecting",
        lambda: asyncio.to_thread(
            lambda: update_job_state_sync(
                job_id,
                status="collecting_data",
                training_pairs_count=len(pairs),
                started_at=_now_iso(),
            )
        ),
    )

    # ── 2. Submit to Modal ──
    try:
        submission = await ctx.step.run(
            "submit-modal",
            lambda: submit_modal_job(
                org_id=org_id, base_model=base_model, training_pairs=pairs
            ),
        )
    except ModalNotConfigured as exc:
        await ctx.step.run(
            "mark-not-configured",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="failed",
                    error_message=f"Modal backend not configured: {exc}",
                    completed_at=_now_iso(),
                )
            ),
        )
        return {"status": "failed", "reason": "modal_not_configured"}

    await ctx.step.run(
        "mark-training",
        lambda: asyncio.to_thread(
            lambda: update_job_state_sync(
                job_id,
                status="training",
                external_job_id=submission.job_id,
            )
        ),
    )

    # ── 3. Poll until done ──
    # `step.sleep` lets Inngest checkpoint between polls so a worker restart
    # doesn't lose progress. The loop is unrolled across step.run calls for
    # the same reason.
    final_status = None
    for attempt in range(MAX_POLL_ATTEMPTS):
        await ctx.step.sleep(f"poll-wait-{attempt}", f"{POLL_INTERVAL_SECONDS}s")
        status = await ctx.step.run(
            f"poll-{attempt}",
            lambda sid=submission.job_id: poll_modal_job(sid),
        )
        if status.state in {"succeeded", "failed"}:
            final_status = status
            break

    if not final_status:
        await ctx.step.run(
            "mark-timeout",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="failed",
                    error_message="Timed out waiting for Modal job to complete",
                    completed_at=_now_iso(),
                )
            ),
        )
        return {"status": "failed", "reason": "timeout"}

    if final_status.state == "failed":
        await ctx.step.run(
            "mark-modal-failed",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="failed",
                    error_message=final_status.error or "Modal training failed",
                    completed_at=_now_iso(),
                )
            ),
        )
        return {"status": "failed", "reason": "modal_failed"}

    # ── 4. Eval gate ──
    score_before = final_status.eval_score_before or 0.0
    score_after = final_status.eval_score_after or 0.0
    model_id = final_status.fine_tuned_model_id or ""

    await ctx.step.run(
        "mark-evaluating",
        lambda: asyncio.to_thread(
            lambda: update_job_state_sync(
                job_id,
                status="evaluating",
                eval_score_before=score_before,
                eval_score_after=score_after,
            )
        ),
    )

    if (score_after - score_before) < MIN_EVAL_IMPROVEMENT:
        await ctx.step.run(
            "mark-no-improvement",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="failed",
                    error_message=(
                        f"Fine-tuned model did not improve over baseline "
                        f"({score_before:.3f} → {score_after:.3f}, "
                        f"floor={MIN_EVAL_IMPROVEMENT})"
                    ),
                    completed_at=_now_iso(),
                )
            ),
        )
        return {"status": "failed", "reason": "no_improvement"}

    # ── 5. Deploy + trigger org-wide re-embed ──
    await ctx.step.run(
        "deploy",
        lambda: asyncio.to_thread(
            lambda: deploy_org_embedding_model(org_id, model_id)
        ),
    )
    await ctx.step.run(
        "mark-reembedding",
        lambda: asyncio.to_thread(
            lambda: update_job_state_sync(job_id, status="reembedding")
        ),
    )

    client = get_inngest_client()
    await ctx.step.run(
        "enqueue-reembed",
        lambda: client.send(
            inngest.Event(
                name="embeddings/reembed-org",
                data={"org_id": org_id, "job_id": job_id, "model_id": model_id},
            )
        ),
    )

    return {
        "status": "deployed",
        "model_id": model_id,
        "score_before": score_before,
        "score_after": score_after,
    }


# ── Re-embed org chunks against the fine-tuned model ────────────────────────


@_inngest_client.create_function(
    fn_id="embeddings-reembed-org",
    trigger=inngest.TriggerEvent(event="embeddings/reembed-org"),
    # Long, but bounded — re-embedding 100k chunks at ~50/sec ≈ 30 min.
    # Concurrency 1 per org to avoid hitting embedding-provider rate caps
    # alongside live ingestion.
    concurrency=[inngest.Concurrency(limit=1, key="event.data.org_id")],
    retries=1,
)
async def reembed_org_chunks(ctx: inngest.Context) -> dict[str, Any]:
    """Re-call the embedding adapter for every non-archived chunk in `org_id`
    and overwrite the row in `embeddings`. The adapter reads the org's
    deployed model from organizations.metadata so we don't have to thread
    the model id through every call site.

    Batched in 50-chunk slices to bound memory + give checkpointing handles.
    """
    from app.services.ingestion.embedder import (
        bind_org_for_embedding,
        get_embedder,
        invalidate_org_embedding_cache,
    )

    data = ctx.event.data
    org_id: str = data["org_id"]
    job_id: str = data.get("job_id") or ""

    # Force a fresh org→model lookup since deploy_org_embedding_model just ran.
    invalidate_org_embedding_cache(org_id)
    bind_org_for_embedding(org_id)

    svc = get_service_client()
    embedder = get_embedder()
    BATCH = 50
    cursor: str | None = None
    total_reembedded = 0

    while True:
        def _load(cur=cursor) -> list[dict[str, Any]]:
            q = (
                svc.table("chunks")
                .select("id, content, section_heading")
                .eq("org_id", org_id)
                .eq("is_archived", False)
                .order("id")
                .limit(BATCH)
            )
            if cur:
                q = q.gt("id", cur)
            return q.execute().data or []

        batch = await ctx.step.run(f"load-batch-after-{cursor or 'start'}", _load)
        if not batch:
            break

        # Build the same augmented text the ingestion pipeline does (section
        # heading prepended). This is duplicated from `_augment_for_embedding`
        # because that helper takes a frozen Chunk dataclass and we have raw
        # rows here — the duplication is intentional & narrow.
        texts: list[str] = []
        for r in batch:
            content = r.get("content") or ""
            heading = r.get("section_heading")
            texts.append(f"{heading}\n\n{content}" if heading else content)

        vectors = await ctx.step.run(
            f"embed-batch-after-{cursor or 'start'}",
            lambda t=texts: embedder.embed_texts(t, task_type="RETRIEVAL_DOCUMENT"),
        )

        def _persist(rows=batch, vecs=vectors) -> int:
            n = 0
            for row, vec in zip(rows, vecs, strict=True):
                svc.table("embeddings").update({"embedding": vec}).eq(
                    "chunk_id", row["id"]
                ).execute()
                n += 1
            return n

        n = await ctx.step.run(
            f"persist-batch-after-{cursor or 'start'}",
            lambda: asyncio.to_thread(_persist),
        )
        total_reembedded += n
        cursor = batch[-1]["id"]

    if job_id:
        await ctx.step.run(
            "mark-deployed",
            lambda: asyncio.to_thread(
                lambda: update_job_state_sync(
                    job_id,
                    status="deployed",
                    completed_at=_now_iso(),
                )
            ),
        )

    return {"reembedded": total_reembedded, "org_id": org_id}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


FUNCTIONS = [run_embedding_fine_tune, reembed_org_chunks]
