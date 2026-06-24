"""Admin CRUD for autoflows + read-only run history (Agent2 Day 1).

The drag-and-drop builder UI is Day 6; this router is the backend that the
Day 6 frontend will hit and that, in the meantime, lets us seed and verify
autoflows from a thin admin list page.

Auth model — same shape as ``routers/collections.py``:
    * All routes require a valid user JWT (``verify_jwt`` dep).
    * Write routes additionally require ``users.role = 'admin'`` for the
      caller's org. Members can READ autoflows so chat surfaces can show
      "this answer fired autoflow X" later.

Public endpoints (mounted at ``/admin/autoflows``):
    GET    /admin/autoflows                       list active + inactive
    GET    /admin/autoflows/{id}                  one row
    POST   /admin/autoflows                       create (admin)
    PATCH  /admin/autoflows/{id}                  partial update (admin)
    DELETE /admin/autoflows/{id}                  delete (admin)
    GET    /admin/autoflows/{id}/runs             read-only history
    POST   /admin/autoflows/{id}/run-now          fire a one-off run for testing (admin)

The shape returned matches ``AutoflowRead`` from app/models/autoflow.py so the
frontend can use the same type that gets used internally.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.models.autoflow import (
    AutoflowCreate,
    AutoflowRead,
    AutoflowRunRead,
    AutoflowUpdate,
)
from app.services.autoflow_service import (
    execute_autoflow_run,
    get_matching_autoflows,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/autoflows", tags=["autoflows"])


# ── Helpers ──────────────────────────────────────────────────────────────


def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(token: str, user_id: str) -> None:
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only.")


def _client_for_org(token: str) -> Client:
    # RLS-scoped client for reads. Writes go through service-role because the
    # CHECK constraints + Pydantic already validate the shape.
    return get_user_client(token)


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce DB row → AutoflowRead-shaped dict so the response is consistent.

    Pydantic round-tripping would catch typos but adds noticeable overhead
    on a list endpoint. Pre-trip the shape here and trust the model only for
    enum coercion.
    """
    return {
        "id": row["id"],
        "org_id": row["org_id"],
        "name": row["name"],
        "description": row.get("description"),
        "trigger_type": row["trigger_type"],
        "trigger_config": row.get("trigger_config") or {},
        "actions": row.get("actions") or [],
        "confidence_threshold": row.get("confidence_threshold"),
        "is_active": bool(row.get("is_active", True)),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_fired_at": row.get("last_fired_at"),
    }


# ── Read ────────────────────────────────────────────────────────────────


@router.get("")
async def list_autoflows(
    include_inactive: bool = Query(default=True),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """List every autoflow for the caller's org.

    Members can read. Sort: most-recently-updated first so an admin editing a
    flow sees their change at the top of the list.
    """
    org_id, _user_id, token = _require_org(current_user)
    client = _client_for_org(token)

    def _fetch() -> list[dict[str, Any]]:
        q = client.table("autoflows").select("*").eq("org_id", org_id)
        if not include_inactive:
            q = q.eq("is_active", True)
        return q.order("updated_at", desc=True).execute().data or []

    rows = await asyncio.to_thread(_fetch)
    return {"autoflows": [_serialize_row(r) for r in rows]}


@router.get("/{autoflow_id}", response_model=AutoflowRead)
async def get_autoflow(
    autoflow_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = _client_for_org(token)

    def _fetch() -> dict[str, Any] | None:
        res = (
            client.table("autoflows")
            .select("*")
            .eq("id", autoflow_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    row = await asyncio.to_thread(_fetch)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Autoflow not found.")
    return _serialize_row(row)


@router.get("/{autoflow_id}/runs")
async def get_run_history(
    autoflow_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _user_id, token = _require_org(current_user)
    client = _client_for_org(token)

    # Confirm the autoflow exists and belongs to this org before listing runs.
    # Saves leaking 'this id has N runs' on a probing request.
    def _fetch_runs() -> list[dict[str, Any]]:
        af = (
            client.table("autoflows")
            .select("id")
            .eq("id", autoflow_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        if not (af and af.data):
            return []
        res = (
            client.table("autoflow_runs")
            .select("*")
            .eq("autoflow_id", autoflow_id)
            .order("started_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []

    rows = await asyncio.to_thread(_fetch_runs)
    # Pydantic-validate so the frontend gets a guaranteed AutoflowRunRead shape
    # (catches any drift between DB state and the contract).
    runs = [AutoflowRunRead(**r).model_dump(mode="json") for r in rows]
    return {"runs": runs}


# ── Write (admin only) ───────────────────────────────────────────────────


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AutoflowRead)
async def create_autoflow(
    body: AutoflowCreate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)

    svc = get_service_client()
    payload = {
        "org_id": org_id,
        "created_by": user_id,
        "name": body.name,
        "description": body.description,
        "trigger_type": body.trigger_type.value,
        "trigger_config": body.trigger_config.model_dump(exclude_none=True),
        "actions": [a.model_dump(mode="json") for a in body.actions],
        "confidence_threshold": body.confidence_threshold,
        "is_active": body.is_active,
    }

    def _insert() -> dict[str, Any]:
        res = svc.table("autoflows").insert(payload).execute()
        rows = res.data or []
        if not rows:
            raise RuntimeError("autoflow insert returned no rows")
        return rows[0]

    row = await asyncio.to_thread(_insert)
    log.info("autoflow_created id=%s org=%s trigger=%s", row["id"], org_id, body.trigger_type.value)
    return _serialize_row(row)


@router.patch("/{autoflow_id}", response_model=AutoflowRead)
async def update_autoflow(
    autoflow_id: str,
    body: AutoflowUpdate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    # Build the update dict from whichever fields are present. Pydantic's
    # exclude_unset is the cleanest signal for "user explicitly set this".
    update: dict[str, Any] = body.model_dump(exclude_unset=True, mode="json")
    if "trigger_type" in update and update["trigger_type"]:
        # mode='json' on enum returns the string already, but the dump emits
        # the AutoflowTriggerType instance for some enum subclasses; coerce.
        update["trigger_type"] = str(update["trigger_type"])
    if "trigger_config" in update and update["trigger_config"]:
        # exclude_none on the inner model so a missing cron doesn't store {}
        # over an existing cron.
        cfg = body.trigger_config
        if cfg is not None:
            update["trigger_config"] = cfg.model_dump(exclude_none=True)
    if not update:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No fields to update.")

    def _update() -> dict[str, Any] | None:
        # Guard against accidental cross-org writes via service-role.
        res = (
            svc.table("autoflows")
            .update(update)
            .eq("id", autoflow_id)
            .eq("org_id", org_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    row = await asyncio.to_thread(_update)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Autoflow not found.")
    return _serialize_row(row)


@router.delete("/{autoflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_autoflow(
    autoflow_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _delete() -> int:
        res = (
            svc.table("autoflows")
            .delete()
            .eq("id", autoflow_id)
            .eq("org_id", org_id)
            .execute()
        )
        return len(res.data or [])

    deleted = await asyncio.to_thread(_delete)
    if deleted == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Autoflow not found.")


# ── One-off test fire ────────────────────────────────────────────────────


@router.post("/{autoflow_id}/run-now")
async def run_autoflow_now(
    autoflow_id: str,
    body: dict[str, Any] | None = None,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Fire a one-off run with an admin-supplied payload.

    Useful for verifying a flow before promoting it. Bypasses the trigger
    matcher entirely — runs the flow regardless of whether the trigger
    payload would have matched its filters in production.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(token, user_id)
    svc = get_service_client()

    def _fetch() -> dict[str, Any] | None:
        res = (
            svc.table("autoflows")
            .select("*")
            .eq("id", autoflow_id)
            .eq("org_id", org_id)
            .maybe_single()
            .execute()
        )
        return res.data if res else None

    autoflow = await asyncio.to_thread(_fetch)
    if not autoflow:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Autoflow not found.")

    payload = (body or {}).get("trigger_payload") or {}
    payload.setdefault("manual_run_by", user_id)

    # Run synchronously inline so the admin gets the result back immediately.
    # An async dispatch would feel "did it work?" and round-tripping through
    # Inngest for a manual test is wasteful.
    result = await execute_autoflow_run(autoflow=autoflow, trigger_payload=payload)
    return result


# ── Trigger-type catalogue (helps the future builder UI) ────────────────


@router.get("/_meta/trigger-types")
async def list_trigger_types() -> dict[str, Any]:
    """Static metadata for the builder UI dropdowns.

    Kept here so the frontend has one canonical source — adding a new trigger
    type later means editing the migration's CHECK + the AutoflowTriggerType
    enum + this list, all from one PR.
    """
    return {
        "trigger_types": [
            {"value": "document_uploaded", "label": "Document uploaded"},
            {"value": "document_ready", "label": "Document ready (ingested)"},
            {"value": "document_failed", "label": "Document failed to ingest"},
            {"value": "query_no_results", "label": "Query returned no results"},
            {"value": "message_feedback_negative", "label": "Negative feedback on a message"},
            {"value": "scheduled", "label": "Scheduled (cron)"},
            {"value": "employee_joined", "label": "Employee joined"},
            {"value": "knowledge_gap_detected", "label": "Knowledge gap detected"},
            {"value": "approval_requested", "label": "Approval requested"},
            {"value": "agent_completed", "label": "Agent run completed"},
            {"value": "compliance_acknowledged", "label": "Compliance acknowledged"},
        ],
        "action_types": [
            {"value": "generate_output", "label": "Generate output (KB-backed)"},
            {"value": "send_email", "label": "Send email"},
            {"value": "post_slack", "label": "Post to Slack"},
            {"value": "create_notion_page", "label": "Create Notion page"},
            {"value": "notify_admin", "label": "Notify admin"},
            {"value": "emit_webhook", "label": "Emit webhook event"},
            {"value": "hold_for_approval", "label": "Hold for approval"},
            {"value": "create_task", "label": "Create task (Asana/Linear) — not yet"},
        ],
    }
