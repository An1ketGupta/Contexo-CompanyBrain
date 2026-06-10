"""Outbound webhook management endpoints (Day 13 / #85 + #99).

Admins create/edit/delete webhooks via these endpoints; the actual delivery
loop lives in the `webhook/deliver` Inngest function. The secret is returned
in the create response ONCE so the admin can stash it in their receiver —
subsequent GETs only echo a masked prefix.

Authz model: list/get is org-member; create/update/delete is admin-only.
Mirrors the same pattern as /organizations/me/ai-settings.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.auth import verify_jwt
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services.webhooks import ALLOWED_EVENTS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _normalize_events(events: list[str]) -> list[str]:
    bad = [e for e in events if e not in ALLOWED_EVENTS]
    if bad:
        raise ValueError(
            f"Unsupported event(s): {bad}. Allowed: {sorted(ALLOWED_EVENTS)}"
        )
    seen: set[str] = set()
    out: list[str] = []
    for e in events:
        if e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


# ── Request models ──────────────────────────────────────────────────────────

class CreateWebhookRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    url: HttpUrl
    events: list[str] = Field(..., min_length=1, max_length=8)
    # If true, the server generates a secret and returns it once. If false,
    # deliveries go out unsigned (Zapier-style catch hooks don't need this).
    generate_secret: bool = True

    @field_validator("events")
    @classmethod
    def _check_events(cls, v: list[str]) -> list[str]:
        return _normalize_events(v)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        cleaned = " ".join(v.split()).strip()
        if not cleaned:
            raise ValueError("Name cannot be empty.")
        return cleaned


class UpdateWebhookRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: HttpUrl | None = None
    events: list[str] | None = Field(default=None, min_length=1, max_length=8)
    is_active: bool | None = None
    # PATCH on rotation: client sends rotate_secret=true to generate a fresh
    # secret (returned in the response once). To clear, send clear_secret.
    rotate_secret: bool = False
    clear_secret: bool = False

    @field_validator("events")
    @classmethod
    def _check_events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _normalize_events(v)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(user_id: str, token: str) -> None:
    user_client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: user_client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can manage webhooks.",
        )


def _mask_secret(secret: str | None) -> str | None:
    """Echo first 4 + last 4 chars when listing (mirrors GitHub UI)."""
    if not secret:
        return None
    if len(secret) <= 12:
        return "•" * len(secret)
    return f"{secret[:4]}{'•' * (len(secret) - 8)}{secret[-4:]}"


def _public_row(row: dict[str, Any], *, full_secret: str | None = None) -> dict[str, Any]:
    """Project a DB row into the shape the API consumer gets."""
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "events": row.get("events") or [],
        "is_active": bool(row.get("is_active", True)),
        # `full_secret` is non-None only on create + rotate.
        "secret": full_secret if full_secret is not None else _mask_secret(row.get("secret")),
        "last_status": row.get("last_status"),
        "last_triggered_at": row.get("last_triggered_at"),
        "created_at": row.get("created_at"),
    }


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value or ""))


# ── List ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_webhooks(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _, token = _require_org(current_user)
    client = get_user_client(token)

    result = await asyncio.to_thread(
        lambda: client.table("webhooks")
        .select(
            "id, name, url, events, is_active, secret, last_status, "
            "last_triggered_at, created_at"
        )
        .order("created_at", desc=True)
        .execute()
    )
    rows = result.data or []
    return {"webhooks": [_public_row(r) for r in rows]}


# ── Create ──────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: CreateWebhookRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)

    new_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(32) if body.generate_secret else None
    svc = get_service_client()

    row = {
        "id": new_id,
        "org_id": org_id,
        "created_by": user_id,
        "name": body.name,
        "url": str(body.url),
        "events": body.events,
        "secret": secret,
        "is_active": True,
    }
    try:
        result = await asyncio.to_thread(
            lambda: svc.table("webhooks").insert(row).execute()
        )
    except Exception as exc:
        log.error("webhook_create_failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create webhook.",
        ) from exc

    db_row = (result.data or [row])[0]
    # IMPORTANT: this is the only place the full secret leaves the server.
    return {"webhook": _public_row(db_row, full_secret=secret)}


# ── Update / rotate / disable ────────────────────────────────────────────────

@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: str,
    body: UpdateWebhookRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    if not _is_uuid(webhook_id):
        raise HTTPException(status_code=400, detail="Invalid webhook id.")
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)

    update: dict[str, Any] = {}
    if body.name is not None:
        update["name"] = " ".join(body.name.split()).strip()
    if body.url is not None:
        update["url"] = str(body.url)
    if body.events is not None:
        update["events"] = body.events
    if body.is_active is not None:
        update["is_active"] = body.is_active

    rotated_secret: str | None = None
    if body.rotate_secret and body.clear_secret:
        raise HTTPException(
            status_code=400,
            detail="rotate_secret and clear_secret are mutually exclusive.",
        )
    if body.rotate_secret:
        rotated_secret = secrets.token_urlsafe(32)
        update["secret"] = rotated_secret
    elif body.clear_secret:
        update["secret"] = None

    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("webhooks")
        .update(update)
        .eq("id", webhook_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Webhook not found.")

    return {"webhook": _public_row(result.data[0], full_secret=rotated_secret)}


# ── Delete ──────────────────────────────────────────────────────────────────

@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: str,
    current_user: dict = Depends(verify_jwt),
) -> None:
    if not _is_uuid(webhook_id):
        raise HTTPException(status_code=400, detail="Invalid webhook id.")
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)

    svc = get_service_client()
    result = await asyncio.to_thread(
        lambda: svc.table("webhooks")
        .delete()
        .eq("id", webhook_id)
        .eq("org_id", org_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Webhook not found.")


# ── Delivery log ────────────────────────────────────────────────────────────

@router.get("/{webhook_id}/deliveries")
async def list_deliveries(
    webhook_id: str,
    limit: int = Query(25, ge=1, le=100),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Most-recent attempts for one webhook. Admins use this to debug
    why their receiver isn't picking up an event."""
    if not _is_uuid(webhook_id):
        raise HTTPException(status_code=400, detail="Invalid webhook id.")
    org_id, _, token = _require_org(current_user)
    client = get_user_client(token)

    # RLS confines reads to the org; we still pass webhook_id explicitly so
    # a user can't enumerate deliveries from another webhook in the same org
    # without knowing its id.
    result = await asyncio.to_thread(
        lambda: client.table("webhook_deliveries")
        .select("id, event, status_code, error, attempt, delivered_at, response_body")
        .eq("webhook_id", webhook_id)
        .order("delivered_at", desc=True)
        .limit(limit)
        .execute()
    )
    return {"deliveries": result.data or []}


# ── Test fire ──────────────────────────────────────────────────────────────

@router.post("/{webhook_id}/test", status_code=status.HTTP_202_ACCEPTED)
async def test_fire_webhook(
    webhook_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Queue a `webhook.test` delivery so the admin can verify the receiver.

    We allow this as a synthetic event regardless of the webhook's configured
    events list — the convention "test from the dashboard" is universal.
    """
    if not _is_uuid(webhook_id):
        raise HTTPException(status_code=400, detail="Invalid webhook id.")
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)

    svc = get_service_client()
    hook = await asyncio.to_thread(
        lambda: svc.table("webhooks")
        .select("id, is_active")
        .eq("id", webhook_id)
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not hook or not hook.data:
        raise HTTPException(status_code=404, detail="Webhook not found.")
    if not hook.data.get("is_active"):
        raise HTTPException(status_code=409, detail="Webhook is disabled.")

    # Side-step the trigger_event guardrails (event whitelist) by sending
    # the deliver event directly with a synthetic payload.
    import inngest
    from app.inngest.client import get_inngest_client

    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="webhook/deliver",
            data={
                "webhook_id": webhook_id,
                "org_id": org_id,
                "event": "webhook.test",
                "payload": {
                    "message": "If you received this, your webhook is wired up correctly.",
                    "triggered_by": user_id,
                },
            },
        )
    )
    return {"status": "queued"}
