"""Gmail integration router (V5 Day 3).

Endpoints:
    GET  /integrations/gmail/connect      — returns Google consent URL
    GET  /integrations/gmail/callback     — code exchange + install
    GET  /integrations/gmail/status       — connected/scope-check (frontend
                                            uses this to decide whether to
                                            show the re-auth banner)
    POST /integrations/gmail/send         — enqueue an outbound email
    DEL  /integrations/gmail              — disconnect

The send endpoint optimistically marks the message's delivery_status as
`queued` and fans out to Inngest. The worker (`gmail_functions.py`) does the
actual API call with retries; failure paths flip status to `failed` with the
error message stored alongside.

Per-user OAuth — every user authorizes their own Gmail. The admin-only check
that gates Drive doesn't apply here: any user can connect their own mailbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import inngest
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_service_client
from app.errors import NoOrganization
from app.inngest.client import get_inngest_client
from app.services.integrations import gmail

log = logging.getLogger(__name__)

router = APIRouter(tags=["gmail"])

_STATE_TTL_SECONDS = 600
_PROVIDER = "gmail"

# Pragma: we want to catch typos like missing "@", not validate against the
# full RFC 5322 grammar. Pydantic's EmailStr pulls in the email-validator
# dependency which isn't in our base image — this regex is the smallest
# correct-enough guard for the API surface.
import re as _re
_EMAIL_REGEX = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _validate_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_REGEX.match(value):
        raise ValueError("must be a valid email address")
    return value


# ── Auth + state helpers (mirroring slack_router) ───────────────────────────

def _require_user(current_user: dict) -> tuple[str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id


def _mint_state(*, user_id: str, org_id: str) -> str:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"{_PROVIDER}.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str) -> tuple[str, str]:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    try:
        prov, user_id, org_id, ts, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed state.") from exc
    if prov != _PROVIDER:
        raise HTTPException(status_code=400, detail="State provider mismatch.")
    payload = f"{prov}.{user_id}.{org_id}.{ts}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid state signature.")
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired — retry.")
    return user_id, org_id


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)


# ── OAuth: connect + callback ───────────────────────────────────────────────

@router.get("/integrations/gmail/connect")
async def gmail_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Gmail integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id)
    return {"auth_url": gmail.build_auth_url(state=state)}


@router.get("/integrations/gmail/callback")
async def gmail_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Google redirects here after consent. We exchange the code, persist
    credentials, then bounce back to /settings/integrations."""
    if error:
        # User declined consent screen.
        return _settings_redirect(error=f"gmail_oauth_{error}")
    if not code:
        return _settings_redirect(error="gmail_oauth_missing_code")

    user_id, org_id = _parse_state(state)
    try:
        payload = await gmail.exchange_code(code=code)
    except Exception as exc:
        log.error("gmail_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="gmail_oauth_failed")
    try:
        await gmail.store_credentials(org_id=org_id, user_id=user_id, token_payload=payload)
    except RuntimeError as exc:
        if str(exc) == "gmail_send_scope_not_granted":
            return _settings_redirect(error="gmail_send_scope_not_granted")
        log.error("gmail_store_credentials_failed: %s", exc)
        return _settings_redirect(error="gmail_store_failed")
    except Exception as exc:
        log.error("gmail_store_credentials_failed: %s", exc)
        return _settings_redirect(error="gmail_store_failed")
    return _settings_redirect(connected="gmail")


# ── Status / scope-check ────────────────────────────────────────────────────

@router.get("/integrations/gmail/status")
async def gmail_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Single endpoint the frontend uses to decide:
        - Show "Connect Gmail" CTA (connected = false)
        - Show "Reconnect for send permission" banner (has_send_scope = false)
        - Show "Send via Gmail" button (both true)
    """
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("email_address, scopes, connected_at, last_used_at")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {
            "connected": False,
            "has_send_scope": False,
            "email_address": None,
        }
    scopes = row.data.get("scopes") or []
    return {
        "connected": True,
        "has_send_scope": gmail.has_send_scope(scopes),
        "email_address": row.data.get("email_address"),
        "connected_at": row.data.get("connected_at"),
        "last_used_at": row.data.get("last_used_at"),
    }


# ── Send ────────────────────────────────────────────────────────────────────

class SendEmailRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    to: str = Field(..., min_length=3, max_length=320)  # RFC 5321 SMTP cap
    subject: str = Field(..., min_length=1, max_length=998)  # RFC 2822 subject cap
    body: str = Field(..., min_length=1, max_length=200_000)
    cc: str | None = Field(default=None, max_length=320)
    reply_to: str | None = Field(default=None, max_length=320)

    @field_validator("to")
    @classmethod
    def _check_to(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("cc", "reply_to")
    @classmethod
    def _check_optional_email(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_email(v)


class SendEmailResponse(BaseModel):
    queued: bool
    job_id: str


@router.post("/integrations/gmail/send", response_model=SendEmailResponse)
async def gmail_send(
    body: SendEmailRequest,
    current_user: dict = Depends(verify_jwt),
) -> SendEmailResponse:
    org_id, user_id = _require_user(current_user)

    # Cheap up-front check so the user gets an actionable 403 immediately
    # rather than discovering via a silent failure after queueing.
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("gmail_integrations")
        .select("id, scopes")
        .eq("org_id", org_id)
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=400, detail="gmail_not_connected")
    scopes = row.data.get("scopes") or []
    if not gmail.has_send_scope(scopes):
        # Frontend pattern-matches on this code to render the re-auth banner.
        raise HTTPException(status_code=403, detail="gmail_send_scope_missing")

    # Verify the message belongs to this org before we touch it. RLS would
    # also block a write, but we want a clean 404 — not a confusing 500.
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, delivery_status")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    existing_delivery = msg.data.get("delivery_status") or {}
    if existing_delivery.get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(timezone.utc).isoformat()

    # Optimistically mark queued so the UI shows "Sending…" immediately.
    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gmail",
                "status": "queued",
                "recipient": body.to,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", body.message_id)
        .execute()
    )

    # Fan out to Inngest. Idempotency id = job_id so duplicate clicks within
    # the dedupe window don't double-send.
    client = get_inngest_client()
    await client.send(
        inngest.Event(
            name="gmail/send-email",
            data={
                "job_id": job_id,
                "message_id": body.message_id,
                "org_id": org_id,
                "user_id": user_id,
                "to": body.to,
                "subject": body.subject,
                "body": body.body,
                "cc": body.cc,
                "reply_to": body.reply_to,
            },
            id=f"gmail-send-{job_id}",
        )
    )
    return SendEmailResponse(queued=True, job_id=job_id)


@router.delete("/integrations/gmail", status_code=status.HTTP_204_NO_CONTENT)
async def gmail_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    await gmail.disconnect(org_id=org_id, user_id=user_id)
