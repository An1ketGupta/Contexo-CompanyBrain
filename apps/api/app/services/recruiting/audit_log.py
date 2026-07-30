"""Append-only audit log for recruiting actions.

Why: the recruiting publish flow makes 4 external writes (ATS, Notion, Slack,
hiring-manager email). When a customer asks "did you really post that job?"
or compliance wants to verify the trail, we need a durable record that
survives the requisition's own state changes — including a later delete.

The table has no FK cascade from job_requisitions so deleting a requisition
leaves its audit records intact (intentional). All writes go through this
module so the row shape stays consistent.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

from app.database import get_service_client

log = logging.getLogger(__name__)

Action = Literal[
    "publish_attempt",
    "ats_publish",
    "notion_create",
    "slack_notify",
    "hiring_manager_email",
    "edit",
    "delete",
    "candidate_sync",
    "archive",
    "unarchive",
]

Status = Literal["success", "failure", "skipped"]


async def write(
    *,
    org_id: str,
    requisition_id: str | None,
    actor_user_id: str | None,
    action: Action,
    status: Status,
    ats_platform: str | None = None,
    status_code: int | None = None,
    request_summary: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Insert one audit row. Best-effort — failures are logged, never raised.

    A failed audit write must NOT cascade-fail the action being audited (you
    don't roll back a real ATS publish because logging it locally crapped out).
    """
    svc = get_service_client()
    row = {
        "org_id": org_id,
        "requisition_id": requisition_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "status": status,
        "ats_platform": ats_platform,
        "status_code": status_code,
        "request_summary": _safe_summary(request_summary or {}),
        "response_summary": _safe_summary(response_summary or {}),
        "error_message": (error_message or "")[:2000] or None,
        "duration_ms": duration_ms,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await asyncio.to_thread(
            lambda: svc.table("recruiting_audit_log").insert(row).execute()
        )
    except Exception as exc:
        log.warning(
            "recruiting.audit.write_failed action=%s req=%s err=%s",
            action,
            requisition_id,
            exc,
        )


def _safe_summary(d: dict[str, Any]) -> dict[str, Any]:
    """Drop any secrets that might have slipped into the audit payload and cap
    blob sizes. We never want a full ATS API response body in the log — just
    the bits useful for a support conversation."""
    blocked = {"api_key", "access_token", "refresh_token", "authorization", "secret"}
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if str(k).lower() in blocked:
            out[k] = "[redacted]"
            continue
        if isinstance(v, str) and len(v) > 1000:
            out[k] = v[:1000] + f"...(+{len(v) - 1000} chars)"
        else:
            out[k] = v
    return out


@asynccontextmanager
async def timed(
    *,
    org_id: str,
    requisition_id: str | None,
    actor_user_id: str | None,
    action: Action,
    ats_platform: str | None = None,
    request_summary: dict[str, Any] | None = None,
):
    """Wrap a block; emit one audit row on exit with success/failure + timing.

    Usage:
        async with audit_log.timed(...) as ctx:
            result = await do_thing()
            ctx.response_summary = {"id": result.id}
            ctx.status_code = 200
    """
    started = time.perf_counter()

    class _Ctx:
        response_summary: dict[str, Any] | None = None
        status_code: int | None = None
        error_override: str | None = None

    ctx = _Ctx()
    try:
        yield ctx
        await write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=actor_user_id,
            action=action,
            status="success",
            ats_platform=ats_platform,
            status_code=ctx.status_code,
            request_summary=request_summary,
            response_summary=ctx.response_summary,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        await write(
            org_id=org_id,
            requisition_id=requisition_id,
            actor_user_id=actor_user_id,
            action=action,
            status="failure",
            ats_platform=ats_platform,
            status_code=ctx.status_code,
            request_summary=request_summary,
            response_summary=ctx.response_summary,
            error_message=ctx.error_override or f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
