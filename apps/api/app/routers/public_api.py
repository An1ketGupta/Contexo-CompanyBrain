"""Public Developer API (Day 15 / #47, extended Agent Day 14).

Surface:
    * POST /v1/query                       — run one chat turn, text + sources
    * GET  /v1/documents                   — list documents
    * GET  /v1/agents                      — list agent types + input schemas
    * POST /v1/agents/{agent_type}/run     — trigger an agent run
    * GET  /v1/agent-runs/{run_id}         — poll status of a triggered run
    * Settings endpoints (under /settings/api-keys) handle CRUD on the keys.

Auth: Bearer cb_live_* token validated via app.services.api_keys.verify_key.
Rate-limit: a per-key sliding window + the org's regular monthly chat quota.
The chat quota lookup is identical to the dashboard's, so a customer doesn't
get to bypass their plan by routing through the API.

Agent triggers (Day 14):
    * Accepts optional `Idempotency-Key` header — retrying with the same key
      produces the same run_id (UUIDv5 over api_key + agent + key), so
      BambooHR retries collide with their previous send instead of firing
      duplicate onboardings.
    * Accepts optional `approver_email` in the body — when set, creates an
      approval row gated by an existing workspace member; the agent fires
      only after they approve via web/email/Slack. The approver MUST be a
      member of the same org; external approvals are out of scope.
    * Accepts optional `webhook_url` — when set, the agent POSTs the result
      back on completion with HMAC-SHA256 signature derived from the api_key.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl

from app.database import get_service_client
from app.errors import RateLimited
from app.services.api_keys import ApiKeyContext, verify_key
from app.services.approvals import (
    mint_token,
    validate_execution_action,
)
from app.services.agent_registry import (
    AGENT_REGISTRY,
    AgentInputError,
    agent_registry_public_view,
    dispatch_api_agent,
    normalize_idempotency_key,
    precreate_agent_run,
    resolve_approver_user_id,
    run_id_from_idempotency_key,
    validate_agent_input,
)
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


# ── /v1/agents ──────────────────────────────────────────────────────────────


@router.get("/agents")
async def public_list_agents(
    ctx: ApiKeyContext = Depends(get_api_context),
) -> dict[str, Any]:
    """Self-describing index of agent types triggerable via /v1/agents/{type}/run.

    No quota burn — this is a read-only metadata endpoint that callers will
    hit during integration setup.
    """
    return {"agents": agent_registry_public_view()}


# ── /v1/agents/{agent_type}/run ─────────────────────────────────────────────


class AgentTriggerRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    output_channels: list[Literal["email", "slack", "notion"]] = Field(
        default_factory=list
    )
    approver_email: str | None = Field(
        default=None,
        max_length=320,
        description=(
            "When set, the agent runs only after this workspace member approves "
            "via the inbox/email/Slack. Must be an existing org member."
        ),
    )
    webhook_url: HttpUrl | None = Field(
        default=None,
        description=(
            "Optional callback URL. We POST agent.completed/agent.failed with "
            "an HMAC-SHA256 signature header derived from your API key."
        ),
    )


class AgentTriggerResponse(BaseModel):
    agent_run_id: str
    status: str  # 'running' | 'pending_approval'
    estimated_completion_seconds: int
    approval_id: str | None = None
    poll_url: str


@router.post(
    "/agents/{agent_type}/run",
    response_model=AgentTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_agent(
    agent_type: str,
    body: AgentTriggerRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: ApiKeyContext = Depends(get_api_context),
) -> AgentTriggerResponse:
    await _enforce_api_quota(ctx)

    # 1. Validate agent type + input against the registry.
    spec = AGENT_REGISTRY.get(agent_type)
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown agent type '{agent_type}'. "
                f"Valid types: {', '.join(sorted(AGENT_REGISTRY.keys()))}"
            ),
        )

    try:
        clean_input = validate_agent_input(agent_type, body.input)
    except AgentInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # 2. Resolve idempotent run_id. If the caller supplied an Idempotency-Key
    #    we derive a deterministic UUID from (api_key, agent_type, key) so
    #    retries collapse into one agent_runs row. Otherwise a fresh uuid4.
    idem_key = normalize_idempotency_key(idempotency_key)
    if idem_key:
        run_id = run_id_from_idempotency_key(
            api_key_id=ctx.id, agent_type=agent_type, idem_key=idem_key
        )
        # Idempotent path: if we already have a row for this run_id, return
        # its current state instead of re-firing. Same UX as Stripe.
        existing = await _get_agent_run_row(run_id=run_id, org_id=ctx.org_id)
        if existing:
            return _agent_run_to_trigger_response(
                run=existing, spec_seconds=spec.estimated_seconds
            )
    else:
        run_id = str(_uuid.uuid4())

    # Relative URL — we don't store the API base URL server-side. Callers
    # already know the host they hit; appending it keeps integrations
    # portable across staging/prod.
    poll_url = f"/v1/agent-runs/{run_id}"

    webhook_url_str = str(body.webhook_url) if body.webhook_url else None

    # 3. Approval gate — if approver_email is set, route via the existing
    #    approvals workflow with channel='agent'. Existing magic-link / Slack
    #    / web resolve flows just work.
    if body.approver_email:
        approver_user_id = await resolve_approver_user_id(
            org_id=ctx.org_id, approver_email=body.approver_email,
        )
        if not approver_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "approver_email must be an existing workspace member of "
                    "this organisation. Invite them first, or omit the field "
                    "to run without approval."
                ),
            )

        # Pre-create the agent_runs row in `pending_approval` so the caller
        # gets a pollable id immediately.
        svc = get_service_client()
        await precreate_agent_run(
            run_id=run_id,
            org_id=ctx.org_id,
            agent_type=agent_type,
            triggered_by="api",
            triggered_by_user_id=None,
            input_data={
                **clean_input,
                "_api_context": {
                    "run_id": run_id,
                    "webhook_url": webhook_url_str,
                    "api_key_id": ctx.id,
                },
            },
        )
        await asyncio.to_thread(
            lambda: svc.table("agent_runs")
            .update({"status": "pending_approval"})
            .eq("id", run_id)
            .execute()
        )

        execution_action = validate_execution_action(
            {
                "channel": "agent",
                "params": {
                    "agent_type": agent_type,
                    "agent_input": clean_input,
                    "output_channels": list(body.output_channels),
                    "webhook_url": webhook_url_str,
                    "api_key_id": ctx.id,
                    "run_id": run_id,
                },
            }
        )

        raw_token, token_hash, expires_at = mint_token()
        approval_row = {
            "org_id": ctx.org_id,
            "message_id": None,
            "requested_by": None,
            "approver_id": approver_user_id,
            "agent_type": agent_type,
            "agent_input": clean_input,
            "agent_run_id": run_id,
            "api_key_id": ctx.id,
            "execution_action": execution_action,
            "preview_text": (
                f"Agent: {agent_type}\n"
                f"Input: {_short_preview(clean_input)}"
            )[:2000],
            "token_hash": token_hash,
            "token_expires_at": expires_at.isoformat(),
            "status": "pending",
        }

        try:
            res = await asyncio.to_thread(
                lambda: svc.table("approvals").insert(approval_row).execute()
            )
            approval_id = (res.data or [{}])[0].get("id")
        except Exception as exc:
            msg = str(exc).lower()
            if (
                "uq_approvals_pending_per_agent_approver" in msg
                or "duplicate key" in msg
            ):
                # Same (agent_type, approver, input) already pending — fetch it.
                existing = await asyncio.to_thread(
                    lambda: svc.table("approvals")
                    .select("id, agent_run_id")
                    .eq("agent_type", agent_type)
                    .eq("approver_id", approver_user_id)
                    .eq("status", "pending")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                row = (existing.data or [None])[0]
                if not row:
                    raise HTTPException(
                        status_code=500, detail="approval_create_failed",
                    ) from exc
                approval_id = row["id"]
                run_id = row.get("agent_run_id") or run_id
            else:
                log.exception("api_agent_approval_create_failed")
                raise HTTPException(
                    status_code=500, detail="approval_create_failed"
                ) from exc

        # Fire the existing approval-requested notification fan-out (email
        # + Slack DM). The raw token rides on the event payload only — never
        # persisted.
        try:
            import inngest
            from app.inngest.client import get_inngest_client

            client = get_inngest_client()
            await client.send(
                inngest.Event(
                    name="approval/requested",
                    data={
                        "approval_id": approval_id,
                        "org_id": ctx.org_id,
                        "raw_token": raw_token,
                    },
                    id=f"approval-requested-{approval_id}",
                )
            )
        except Exception as exc:
            log.warning(
                "api_agent_approval_notify_failed approval=%s err=%s",
                approval_id,
                exc,
            )

        return AgentTriggerResponse(
            agent_run_id=run_id,
            status="pending_approval",
            estimated_completion_seconds=0,
            approval_id=approval_id,
            poll_url=poll_url,
        )

    # 4. No approver — pre-create the agent_runs row and fire immediately.
    await precreate_agent_run(
        run_id=run_id,
        org_id=ctx.org_id,
        agent_type=agent_type,
        triggered_by="api",
        triggered_by_user_id=None,
        input_data={
            **clean_input,
            "_api_context": {
                "run_id": run_id,
                "webhook_url": webhook_url_str,
                "api_key_id": ctx.id,
            },
        },
    )

    try:
        await dispatch_api_agent(
            org_id=ctx.org_id,
            agent_type=agent_type,
            agent_input=clean_input,
            output_channels=list(body.output_channels),
            webhook_url=webhook_url_str,
            api_key_id=ctx.id,
            approval_id=None,
            run_id=run_id,
        )
    except AgentInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("api_agent_dispatch_failed agent=%s", agent_type)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Couldn't queue the agent. Try again.",
        ) from exc

    return AgentTriggerResponse(
        agent_run_id=run_id,
        status="running",
        estimated_completion_seconds=spec.estimated_seconds,
        approval_id=None,
        poll_url=poll_url,
    )


# ── /v1/agent-runs/{run_id} ─────────────────────────────────────────────────


class AgentRunPublicView(BaseModel):
    id: str
    agent_type: str
    status: str
    output: dict[str, Any]
    error: str | None
    steps: list[dict[str, Any]]
    created_at: str
    completed_at: str | None
    approval_id: str | None


@router.get("/agent-runs/{run_id}", response_model=AgentRunPublicView)
async def get_agent_run(
    run_id: str,
    ctx: ApiKeyContext = Depends(get_api_context),
) -> AgentRunPublicView:
    row = await _get_agent_run_row(run_id=run_id, org_id=ctx.org_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="agent_run_not_found",
        )
    return AgentRunPublicView(
        id=row["id"],
        agent_type=row["agent_type"],
        status=row["status"],
        output=row.get("output") or {},
        error=row.get("error"),
        steps=row.get("steps") or [],
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        approval_id=row.get("approval_id"),
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _get_agent_run_row(*, run_id: str, org_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("agent_runs")
        .select(
            "id, agent_type, status, output, error, steps, created_at, "
            "completed_at, approval_id"
        )
        .eq("id", run_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    return res.data if (res and res.data) else None


def _agent_run_to_trigger_response(
    *, run: dict[str, Any], spec_seconds: int
) -> AgentTriggerResponse:
    poll_url = f"/v1/agent-runs/{run['id']}"
    return AgentTriggerResponse(
        agent_run_id=run["id"],
        status="pending_approval"
        if run["status"] == "pending_approval"
        else ("running" if run["status"] == "running" else run["status"]),
        estimated_completion_seconds=0 if run["status"] != "running" else spec_seconds,
        approval_id=run.get("approval_id"),
        poll_url=poll_url,
    )


def _short_preview(value: dict[str, Any], limit: int = 240) -> str:
    """Compact human preview of the input dict for the approval inbox card."""
    parts: list[str] = []
    for k, v in value.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}={v}")
        if sum(len(p) for p in parts) > limit:
            break
    out = ", ".join(parts)
    return out[: limit - 1] + "…" if len(out) > limit else out
