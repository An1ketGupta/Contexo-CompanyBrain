"""Public Developer API (Day 15 / #47).

Surface:
    * POST /v1/query              — run one chat turn, return text + sources
    * GET  /v1/documents          — list documents
    * Settings endpoints (under /settings/api-keys) handle CRUD on the keys.

Auth: Bearer cb_live_* token validated via app.services.api_keys.verify_key.
Rate-limit: a per-key sliding window + the org's regular monthly chat quota.
The chat quota lookup is identical to the dashboard's, so a customer doesn't
get to bypass their plan by routing through the API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.database import get_service_client
from app.errors import RateLimited
from app.services.api_keys import ApiKeyContext, verify_key
from app.services.llm.task_chain import execute_task_blocking
from app.services.rate_limit import (
    _monthly_check_and_increment,
    _sliding_window_check,
    get_org_plan,
    monthly_budget_for,
)
from app.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["public_api"])


# ── Auth dependency ─────────────────────────────────────────────────────────

async def get_api_context(
    authorization: str | None = Header(default=None),
) -> ApiKeyContext:
    try:
        return await verify_key(authorization)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── Rate limiting (per-key + per-org-monthly) ───────────────────────────────

async def _enforce_api_quota(ctx: ApiKeyContext) -> None:
    settings = get_settings()
    # Per-key sliding window. Catches a runaway script independently of the
    # per-org monthly budget so one bad cron job doesn't burn the whole
    # month in 5 minutes.
    per_key = await _sliding_window_check(
        namespace="api:key",
        identifier=ctx.id,
        limit=settings.rate_limit_api_per_key_per_minute,
        window_seconds=60,
    )
    if not per_key.allowed:
        raise RateLimited(
            message=(
                f"API key rate limit ({settings.rate_limit_api_per_key_per_minute}/min) exceeded. "
                f"Retry in {per_key.reset_seconds}s."
            ),
            retry_after=per_key.reset_seconds,
        )
    # Same monthly bucket as dashboard chat — keeps plan enforcement honest.
    plan = await get_org_plan(ctx.org_id)
    budget = monthly_budget_for(plan)
    monthly = await _monthly_check_and_increment(org_id=ctx.org_id, limit=budget)
    if not monthly.allowed:
        from app.errors import QuotaExceeded
        raise QuotaExceeded(
            message=(
                f"Monthly quota exceeded on the {plan} plan. "
                f"Resets in {max(monthly.seconds_until_reset // 86_400, 1)} day(s)."
            ),
            retry_after=monthly.seconds_until_reset,
            details={"plan": plan, "used": monthly.used, "limit": monthly.limit},
        )


# ── /v1/query ───────────────────────────────────────────────────────────────

class PublicQueryRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=16_000)
    # Optional: pin retrieval to one document, same semantics as the dashboard.
    document_id: str | None = Field(default=None, min_length=8, max_length=64)


class PublicQuerySource(BaseModel):
    document_id: str | None
    document_name: str
    page_number: int | None
    section_heading: str | None
    excerpt: str


class PublicQueryResponse(BaseModel):
    output: str
    sources: list[PublicQuerySource]
    tool_calls: int


@router.post("/query", response_model=PublicQueryResponse)
async def public_query(
    body: PublicQueryRequest,
    ctx: ApiKeyContext = Depends(get_api_context),
) -> PublicQueryResponse:
    """Run a single chat turn against the org's knowledge base.

    Non-streaming. For long answers we still resolve in one HTTP response —
    a streaming variant lands once we have customers asking for it.
    """
    await _enforce_api_quota(ctx)

    # Use the service-role client because there's no user JWT to thread RLS
    # through. The orchestrator's `db_client` param is used inside searches;
    # org_id is the security boundary, enforced at every retrieval call.
    from app.database import get_service_client as _svc
    db_client = _svc()

    result = await execute_task_blocking(
        user_message=body.message,
        org_id=ctx.org_id,
        db_client=db_client,
        history=None,
    )

    if result.error:
        raise HTTPException(status_code=502, detail=result.error)

    sources = [
        PublicQuerySource(
            document_id=s.get("document_id"),
            document_name=s.get("document_name") or "Unknown",
            page_number=s.get("page_number"),
            section_heading=s.get("section_heading"),
            excerpt=(s.get("excerpt") or "")[:500],
        )
        for s in (result.sources or [])
    ]
    return PublicQueryResponse(
        output=result.text or "",
        sources=sources,
        tool_calls=result.tool_calls_made,
    )


# ── /v1/documents ───────────────────────────────────────────────────────────

@router.get("/documents")
async def public_list_documents(
    limit: int = Query(50, ge=1, le=200),
    status_filter: str | None = Query(None, alias="status"),
    ctx: ApiKeyContext = Depends(get_api_context),
) -> dict[str, Any]:
    """Paginated document listing for the calling org."""
    await _enforce_api_quota(ctx)
    svc = get_service_client()

    def _run() -> Any:
        q = (
            svc.table("documents")
            .select(
                "id, name, file_type, status, chunk_count, created_at, source, external_id"
            )
            .eq("org_id", ctx.org_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status_filter:
            q = q.eq("status", status_filter)
        return q.execute()

    res = await asyncio.to_thread(_run)
    return {"documents": res.data or [], "limit": limit}
