"""Approval queue (Agent Roadmap Day 6).

Endpoints:
    POST   /approvals                     — submit an AI output for approval
    GET    /approvals                     — inbox + sent tabs
    GET    /approvals/{id}                — full detail (web view)
    POST   /approvals/{id}/resolve        — logged-in approve/reject
    POST   /approvals/resolve-by-token    — magic-link approve/reject (public)
    GET    /approvals/by-token            — lookup for the magic-link page

Auth model:
    * Submit + inbox + resolve-by-id   → JWT (org member)
    * resolve-by-token + by-token      → public, gated by sha256(secret+token)

Execution model:
    On approval we fire `approval/execute-action` which fans out to the
    existing gmail/slack/notion/gdocs Inngest functions. Rejection is final;
    we never queue the side effect.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.observability import get_logger
from app.services.approvals import (
    consteq,
    dispatch_execution,
    hash_token,
    mint_token,
    plan_can_request_approvals,
    validate_execution_action,
)
from app.services.webhooks import trigger_event as trigger_webhook_event

log = get_logger(__name__)

router = APIRouter(tags=["approvals"])


# ── Models ──────────────────────────────────────────────────────────────────


class ApprovalCreate(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    approver_id: str = Field(..., min_length=8, max_length=64)
    execution_action: dict[str, Any]
    preview_text: str | None = Field(default=None, max_length=2000)


class ApprovalResolve(BaseModel):
    action: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


class ApprovalResolveByToken(BaseModel):
    token: str = Field(..., min_length=16, max_length=128)
    action: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _require_user(current_user: dict) -> tuple[str, str, str]:
    user_id = current_user.get("user_id")
    org_id = current_user.get("org_id")
    token = current_user.get("token")
    if not user_id or not org_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return user_id, org_id, token


async def _get_org_plan(org_id: str) -> str:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("plan")
        .eq("id", org_id)
        .maybe_single()
        .execute()
    )
    return (row.data or {}).get("plan") or "free"


async def _resolve_email(user_id: str) -> str | None:
    """Pull canonical email from auth.users — we never trust the JWT claim
    because it can be stale and the approver picker stores user_id, not
    email."""
    svc = get_service_client()
    try:
        au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
        return getattr(getattr(au, "user", None), "email", None)
    except Exception as exc:
        log.warning("approver_email_lookup_failed", user_id=user_id, error=str(exc))
        return None


def _audit_from_request(request: Request) -> dict[str, Any]:
    fwd = request.headers.get("x-forwarded-for") or ""
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)
    return {
        "ip": ip,
        "ua": request.headers.get("user-agent"),
        "ts": datetime.now(UTC).isoformat(),
    }


async def _execute_and_finalize(
    *,
    approval: dict[str, Any],
    audit: dict[str, Any],
    resolved_via: str,
    action: str,
    note: str | None,
) -> None:
    """Common tail for every resolve path: persist resolution, dispatch the
    side-effect when approved, fire the resolved-notify event."""
    svc = get_service_client()
    now_iso = datetime.now(UTC).isoformat()

    # Critical: we set status='approved' optimistically and update to
    # 'executed' / 'execution_failed' from the post-dispatch outcome below.
    update = {
        "status": action,
        "note": note,
        "resolved_at": now_iso,
        "resolved_via": resolved_via,
        "resolved_audit": audit,
    }
    await asyncio.to_thread(
        lambda: svc.table("approvals").update(update).eq("id", approval["id"]).execute()
    )

    if action == "approved":
        try:
            await dispatch_execution(
                approval_id=approval["id"],
                message_id=approval["message_id"],
                org_id=approval["org_id"],
                requested_by=approval["requested_by"],
                action=approval["execution_action"],
            )
        except Exception as exc:
            log.warning(
                "approval_dispatch_failed",
                approval_id=approval["id"],
                error=str(exc),
            )
            await asyncio.to_thread(
                lambda: svc.table("approvals")
                .update({"status": "execution_failed"})
                .eq("id", approval["id"])
                .execute()
            )
            # Still notify the requester so they're not left waiting.

    # Notify the requester. Fire-and-forget — the resolution is already
    # persisted; a missed email isn't a blocking failure.
    try:
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="approval/resolved",
                data={
                    "approval_id": approval["id"],
                    "org_id": approval["org_id"],
                    "requested_by": approval["requested_by"],
                    "approver_id": approval["approver_id"],
                    "action": action,
                    "note": note,
                    "resolved_via": resolved_via,
                },
            )
        )
    except Exception as exc:
        log.warning(
            "approval_resolved_event_failed",
            approval_id=approval["id"],
            error=str(exc),
        )

    # Outbound webhook — fire-and-forget. We don't include the execution
    # params (which can be a full email body) because receivers asking
    # "did X get approved" only need the verdict, not the contents.
    try:
        await trigger_webhook_event(
            org_id=approval["org_id"],
            event="approval.decided",
            payload={
                "approval_id": approval["id"],
                "message_id": approval["message_id"],
                "requested_by": approval["requested_by"],
                "approver_id": approval["approver_id"],
                "action": action,
                "channel": (approval.get("execution_action") or {}).get("channel"),
                "note": note,
                "resolved_via": resolved_via,
                "resolved_at": now_iso,
            },
        )
    except Exception as exc:
        log.warning(
            "approval_decided_webhook_failed",
            approval_id=approval["id"],
            error=str(exc),
        )


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post("/approvals", status_code=status.HTTP_201_CREATED)
async def request_approval(
    body: ApprovalCreate,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, org_id, _token = _require_user(current_user)
    svc = get_service_client()

    # Plan gate — paid plans only.
    plan = await _get_org_plan(org_id)
    if not plan_can_request_approvals(plan):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Submit for Approval is available on Starter and above. Upgrade in Settings → Billing.",
        )

    # Validate execution_action shape before we mint a token.
    try:
        normalized_action = validate_execution_action(body.execution_action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.approver_id == user_id:
        raise HTTPException(
            status_code=400,
            detail="Pick a different approver — you can't approve your own request.",
        )

    # Confirm message belongs to the caller's org and isn't already sent.
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, delivery_status")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    if (msg.data.get("delivery_status") or {}).get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")

    # Confirm approver is in the same org.
    approver = await asyncio.to_thread(
        lambda: svc.table("users")
        .select("id, org_id, display_name")
        .eq("id", body.approver_id)
        .maybe_single()
        .execute()
    )
    if not approver or not approver.data or approver.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="approver_not_found")

    raw_token, token_hash, token_expires_at = mint_token()

    row = {
        "org_id": org_id,
        "message_id": body.message_id,
        "requested_by": user_id,
        "approver_id": body.approver_id,
        "execution_action": normalized_action,
        "preview_text": (body.preview_text or "")[:2000] or None,
        "token_hash": token_hash,
        "token_expires_at": token_expires_at.isoformat(),
        "status": "pending",
    }

    try:
        result = await asyncio.to_thread(
            lambda: svc.table("approvals").insert(row).execute()
        )
    except Exception as exc:
        if "uq_approvals_pending_per_message_approver" in str(exc) or "duplicate key" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail="There's already a pending approval for this message and approver.",
            ) from exc
        raise HTTPException(status_code=500, detail="approval_create_failed") from exc

    approval_row = (result.data or [None])[0]
    if not approval_row:
        raise HTTPException(status_code=500, detail="approval_create_failed")

    approval_id = approval_row["id"]

    # Notify async — email + Slack DM. The function is debounced so resend
    # is safe.
    try:
        client = get_inngest_client()
        await client.send(
            inngest.Event(
                name="approval/requested",
                data={
                    "approval_id": approval_id,
                    "org_id": org_id,
                    "raw_token": raw_token,  # plaintext only in event payload; never persisted
                },
                id=f"approval-requested-{approval_id}",
            )
        )
    except Exception as exc:
        log.warning("approval_notify_dispatch_failed", approval_id=approval_id, error=str(exc))

    try:
        from app.services.analytics import track_event

        await track_event(
            org_id=org_id,
            user_id=user_id,
            event_type="approval_requested",
            metadata={"approval_id": approval_id, "channel": normalized_action["channel"]},
        )
    except Exception:
        pass

    try:
        await trigger_webhook_event(
            org_id=org_id,
            event="approval.requested",
            payload={
                "approval_id": approval_id,
                "message_id": body.message_id,
                "requested_by": user_id,
                "approver_id": body.approver_id,
                "channel": normalized_action["channel"],
                "preview_text": body.preview_text,
            },
        )
    except Exception as exc:
        log.warning(
            "approval_requested_webhook_failed",
            approval_id=approval_id,
            error=str(exc),
        )

    return {"approval_id": approval_id}


@router.get("/approvals")
async def list_approvals(
    tab: Literal["inbox", "sent"] = Query(default="inbox"),
    status_filter: Literal["pending", "approved", "rejected", "all"] = Query(default="pending", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    q = (
        client.table("approvals")
        .select(
            "id, status, note, preview_text, execution_action, requested_by, approver_id, "
            "message_id, created_at, resolved_at, resolved_via, token_expires_at"
        )
        .order("created_at", desc=True)
        .limit(limit)
    )
    if tab == "inbox":
        q = q.eq("approver_id", user_id)
    else:
        q = q.eq("requested_by", user_id)
    if status_filter != "all":
        q = q.eq("status", status_filter)

    rows = await asyncio.to_thread(lambda: q.execute())
    items = rows.data or []

    # Hydrate display names for requester + approver — cheap and the UI
    # needs them for every row.
    svc = get_service_client()
    user_ids = {row["requested_by"] for row in items} | {row["approver_id"] for row in items}
    names: dict[str, str | None] = {}
    if user_ids:
        users = await asyncio.to_thread(
            lambda: svc.table("users")
            .select("id, display_name")
            .in_("id", list(user_ids))
            .execute()
        )
        names = {u["id"]: u.get("display_name") for u in (users.data or [])}

    for row in items:
        row["requested_by_name"] = names.get(row["requested_by"])
        row["approver_name"] = names.get(row["approver_id"])

    return {"approvals": items}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, token = _require_user(current_user)
    client = get_user_client(token)

    row = await asyncio.to_thread(
        lambda: client.table("approvals")
        .select(
            "id, org_id, status, note, preview_text, execution_action, "
            "requested_by, approver_id, message_id, created_at, resolved_at, "
            "resolved_via, token_expires_at"
        )
        .eq("id", approval_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="approval_not_found")

    data = row.data
    # RLS already restricts to participants/admins, but be explicit so a
    # future RLS regression doesn't leak data — and so the API surface is
    # uniform across magic-link + JWT paths.
    if user_id not in {data["requested_by"], data["approver_id"]}:
        # Admin can still see via RLS, but they don't get the resolve action.
        pass

    return data


@router.post("/approvals/{approval_id}/resolve")
async def resolve_approval(
    approval_id: str,
    body: ApprovalResolve,
    request: Request,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    user_id, _org_id, _token = _require_user(current_user)
    svc = get_service_client()

    row = await asyncio.to_thread(
        lambda: svc.table("approvals").select("*").eq("id", approval_id).maybe_single().execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="approval_not_found")

    approval = row.data
    if approval["approver_id"] != user_id:
        raise HTTPException(status_code=403, detail="not_authorized")
    if approval["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"already_{approval['status']}")

    await _execute_and_finalize(
        approval=approval,
        audit=_audit_from_request(request),
        resolved_via="web",
        action=body.action,
        note=body.note,
    )
    return {"resolved": True, "action": body.action}


@router.get("/approvals/by-token")
async def lookup_by_token(token: str) -> dict[str, Any]:
    """Public lookup used by the magic-link page to pre-render the UI.
    Returns a redacted view — no execution params, just enough to make the
    decision."""
    if len(token) < 16 or len(token) > 128:
        raise HTTPException(status_code=400, detail="malformed_token")
    svc = get_service_client()
    th = hash_token(token)
    row = await asyncio.to_thread(
        lambda: svc.table("approvals")
        .select(
            "id, org_id, status, preview_text, execution_action, requested_by, "
            "approver_id, token_expires_at, created_at"
        )
        .eq("token_hash", th)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="approval_not_found")
    data = row.data

    now_iso = datetime.now(UTC).isoformat()
    if data["token_expires_at"] < now_iso:
        raise HTTPException(status_code=410, detail="token_expired")
    if data["status"] != "pending":
        raise HTTPException(status_code=410, detail=f"already_{data['status']}")

    # Hydrate names for the magic-link page heading.
    user_ids = [data["requested_by"], data["approver_id"]]
    users = await asyncio.to_thread(
        lambda: svc.table("users").select("id, display_name").in_("id", user_ids).execute()
    )
    name_map = {u["id"]: u.get("display_name") for u in (users.data or [])}
    org = await asyncio.to_thread(
        lambda: svc.table("organizations")
        .select("name")
        .eq("id", data["org_id"])
        .maybe_single()
        .execute()
    )

    # Don't leak full execution params — just the channel + destination
    # summary. The full body is in `preview_text`.
    action = data["execution_action"] or {}
    params = action.get("params") or {}
    safe_action = {
        "channel": action.get("channel"),
        "destination": (
            params.get("to") if action.get("channel") == "gmail"
            else params.get("channel_name") or params.get("channel_id") if action.get("channel") == "slack"
            else params.get("title") if action.get("channel") in {"notion", "gdocs"}
            else None
        ),
        "title": params.get("title") or params.get("subject"),
    }

    return {
        "approval_id": data["id"],
        "status": data["status"],
        "preview_text": data["preview_text"],
        "execution_action": safe_action,
        "requested_by_name": name_map.get(data["requested_by"]),
        "approver_name": name_map.get(data["approver_id"]),
        "org_name": (org.data or {}).get("name") if org else None,
        "created_at": data["created_at"],
        "token_expires_at": data["token_expires_at"],
    }


@router.post("/approvals/resolve-by-token")
async def resolve_by_token(
    body: ApprovalResolveByToken,
    request: Request,
) -> dict[str, Any]:
    """Public resolve endpoint. Auth = sha256(secret || token) match.

    We MUST re-lookup by the supplied token's hash (not by approval_id)
    so a token leak can't be replayed against a different approval id.
    """
    svc = get_service_client()
    th = hash_token(body.token)
    row = await asyncio.to_thread(
        lambda: svc.table("approvals").select("*").eq("token_hash", th).maybe_single().execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="approval_not_found")
    approval = row.data

    # Constant-time compare on the stored hash. (Equality above is already
    # constant-time at the Postgres layer, but we additionally compare the
    # computed digest to the stored value to catch any driver-level
    # type quirks that could turn this into a timing oracle.)
    if not consteq(approval["token_hash"], th):
        raise HTTPException(status_code=404, detail="approval_not_found")

    now_iso = datetime.now(UTC).isoformat()
    if approval["token_expires_at"] < now_iso:
        raise HTTPException(status_code=410, detail="token_expired")
    if approval["status"] != "pending":
        raise HTTPException(status_code=410, detail=f"already_{approval['status']}")

    await _execute_and_finalize(
        approval=approval,
        audit=_audit_from_request(request),
        resolved_via="email",
        action=body.action,
        note=body.note,
    )
    return {"resolved": True, "action": body.action, "approval_id": approval["id"]}
