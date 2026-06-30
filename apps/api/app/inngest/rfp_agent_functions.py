"""Inngest pipeline for the RfpAgent.

Events:
  rfp/uploaded               — fresh source file persisted → extract requirements
  rfp/requirements-approved  — rep approved the parsed/edited list → draft answers
  rfp/regenerate-answer      — rep asked to regenerate one answer
  rfp/rep-approved           — rep approved all answers → legal review or finalize
  rfp/legal-approved         — legal approved the package → finalize
  rfp/legal-rejected         — legal rejected with notes → redraft flagged
  rfp/finalize               — render outputs + upload
  rfp/resume                 — generic re-kick (used by the router after a state mutation)

Concurrency: per-rfp_id key so events for the same RFP queue serially.
Retries: 2 per function (matches Sales Agent). Long-running steps inside the
agent are wrapped in step.run() at the agent level rather than per-function.
"""
from __future__ import annotations

from typing import Any

import inngest

from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.agents.rfp_agent import RfpAgent

log = get_logger(__name__)
_inngest_client = get_inngest_client()


# ── Driver helper ──────────────────────────────────────────────────────────


async def _drive(
    *,
    rfp_id: str,
    org_id: str,
    triggered_by_user_id: str | None = None,
    resume_from: str | None = None,
) -> dict[str, Any]:
    agent = RfpAgent(
        org_id=org_id,
        rfp_id=rfp_id,
        triggered_by_user_id=triggered_by_user_id,
        resume_from=resume_from,
    )
    try:
        return await agent.run_safely()
    except Exception as exc:
        log.warning("rfp.agent_run_failed rfp=%s err=%s", rfp_id, exc)
        raise


# ── Event handlers ─────────────────────────────────────────────────────────


@_inngest_client.create_function(
    fn_id="rfp-uploaded",
    trigger=inngest.TriggerEvent(event="rfp/uploaded"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_uploaded(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped", "reason": "missing_ids"}
    return await _drive(
        rfp_id=rfp_id,
        org_id=org_id,
        triggered_by_user_id=data.get("triggered_by_user_id"),
        resume_from="extracting",
    )


@_inngest_client.create_function(
    fn_id="rfp-requirements-approved",
    trigger=inngest.TriggerEvent(event="rfp/requirements-approved"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_requirements_approved(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id, resume_from="drafting")


@_inngest_client.create_function(
    fn_id="rfp-regenerate-answer",
    trigger=inngest.TriggerEvent(event="rfp/regenerate-answer"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=2, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_regenerate_answer(ctx: inngest.Context) -> dict[str, Any]:
    """Re-run the answerer for a single requirement. The router has already
    marked the row as 'rejected' so the agent treats it as a legal redraft."""
    from app.database import get_service_client
    from app.services.agents.rfp_agent import answerer as answerer_svc
    from app.services.retrieval.collection_scope import resolve_org_rfp_scope
    from datetime import UTC, datetime
    import asyncio

    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    requirement_id = data.get("requirement_id")
    legal_feedback = data.get("legal_feedback")
    if not rfp_id or not org_id or not requirement_id:
        return {"status": "skipped"}

    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_requirements")
            .select("*")
            .eq("id", requirement_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    req = await asyncio.to_thread(_fetch)
    if not req:
        return {"status": "skipped", "reason": "requirement_not_found"}

    def _fetch_rfp() -> dict[str, Any] | None:
        res = (
            svc.table("rfp_responses")
            .select("collection_id")
            .eq("id", rfp_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    rfp_row = await asyncio.to_thread(_fetch_rfp)
    scope_doc_ids, _ = await resolve_org_rfp_scope(
        org_id=org_id,
        override_collection_id=(rfp_row or {}).get("collection_id"),
    )

    ans = await answerer_svc.answer_requirement(
        org_id=org_id,
        requirement_text=req["requirement_text"],
        category=req.get("category"),
        scope_document_ids=scope_doc_ids,
        legal_rejection_feedback=legal_feedback,
    )

    row = {
        "requirement_id": requirement_id,
        "rfp_id": rfp_id,
        "org_id": org_id,
        "answer_text": ans.answer_text[:8000] if ans.answer_text else None,
        "sources": [
            {
                "document_id": s.document_id,
                "document_name": s.document_name,
                "chunk_id": s.chunk_id,
                "similarity": float(s.similarity),
                "snippet": s.snippet,
            }
            for s in ans.sources
        ],
        "confidence": float(ans.confidence) if ans.confidence else None,
        "confidence_band": ans.confidence_band,
        "search_queries": ans.queries,
        "status": "gap" if ans.is_gap else "draft",
        "flag_message": ans.flag_message,
        "generated_at": datetime.now(UTC).isoformat(),
    }

    def _upsert() -> None:
        svc.table("rfp_answers").upsert(row, on_conflict="requirement_id").execute()

    await asyncio.to_thread(_upsert)
    return {"status": "ok", "is_gap": ans.is_gap}


@_inngest_client.create_function(
    fn_id="rfp-rep-approved",
    trigger=inngest.TriggerEvent(event="rfp/rep-approved"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_rep_approved(ctx: inngest.Context) -> dict[str, Any]:
    """Router already wrote awaiting_legal_review OR finalizing on the row
    based on rfp.legal_required. We just drive the agent from there."""
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id)


@_inngest_client.create_function(
    fn_id="rfp-legal-approved",
    trigger=inngest.TriggerEvent(event="rfp/legal-approved"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_legal_approved(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id, resume_from="finalizing")


@_inngest_client.create_function(
    fn_id="rfp-legal-rejected",
    trigger=inngest.TriggerEvent(event="rfp/legal-rejected"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_legal_rejected(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id, resume_from="legal_rejected")


@_inngest_client.create_function(
    fn_id="rfp-finalize",
    trigger=inngest.TriggerEvent(event="rfp/finalize"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_finalize(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id, resume_from="finalizing")


@_inngest_client.create_function(
    fn_id="rfp-resume",
    trigger=inngest.TriggerEvent(event="rfp/resume"),
    retries=2,
    concurrency=[
        inngest.Concurrency(limit=1, key="event.data.rfp_id", scope="fn"),
    ],
)
async def rfp_resume(ctx: inngest.Context) -> dict[str, Any]:
    data = ctx.event.data
    rfp_id = data.get("rfp_id")
    org_id = data.get("org_id")
    resume_from = data.get("resume_from")
    if not rfp_id or not org_id:
        return {"status": "skipped"}
    return await _drive(rfp_id=rfp_id, org_id=org_id, resume_from=resume_from)


FUNCTIONS: list[Any] = [
    rfp_uploaded,
    rfp_requirements_approved,
    rfp_regenerate_answer,
    rfp_rep_approved,
    rfp_legal_approved,
    rfp_legal_rejected,
    rfp_finalize,
    rfp_resume,
]
