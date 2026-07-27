"""Support mailbox OAuth router (org-level).

    GET    /integrations/support-mailbox/connect     — Google consent URL
    GET    /integrations/support-mailbox/callback    — code exchange + install
    GET    /integrations/support-mailbox/status      — connected/scope state
    POST   /integrations/support-mailbox/poll        — force a poll now
    DELETE /integrations/support-mailbox             — disconnect

Admin-only, unlike the per-user Gmail flow: this is the address the whole org's
customers write to, and its token can read every message in that inbox.

The callback lands on its own redirect URI so Google's response is never routed
into `gmail_callback`, which mints and expects a `gmail` state.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_user_client
from app.errors import NoOrganization
from app.services.integrations import support_mailbox

log = logging.getLogger(__name__)

router = APIRouter(tags=["support-mailbox"])

_STATE_TTL_SECONDS = 600
_PROVIDER = "support_mailbox"


def _require_user(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(*, user_id: str, token: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can manage the support mailbox.",
        )


def _mint_state(*, user_id: str, org_id: str) -> str:
    secret = get_settings().oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"{_PROVIDER}.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str) -> tuple[str, str]:
    secret = get_settings().oauth_state_secret
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


def _settings_redirect(
    *, connected: str | None = None, error: str | None = None
) -> RedirectResponse:
    # Back to where the admin started, not the per-user integrations page.
    base = get_settings().app_url.rstrip("/") + "/admin/support/settings"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)


@router.get("/integrations/support-mailbox/connect")
async def support_mailbox_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(
            status_code=503, detail="Google OAuth is not configured on this deploy."
        )
    state = _mint_state(user_id=user_id, org_id=org_id)
    return {"auth_url": support_mailbox.build_auth_url(state=state)}


@router.get("/integrations/support-mailbox/callback")
async def support_mailbox_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error:
        return _settings_redirect(error=f"support_mailbox_oauth_{error}")
    if not code:
        return _settings_redirect(error="support_mailbox_oauth_missing_code")

    user_id, org_id = _parse_state(state)
    try:
        payload = await support_mailbox.exchange_code(code=code)
    except Exception as exc:
        log.error("support_mailbox_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="support_mailbox_oauth_failed")

    try:
        await support_mailbox.store_credentials(
            org_id=org_id, connected_by=user_id, token_payload=payload
        )
    except RuntimeError as exc:
        # Scope refusals get their own codes so the UI can say which permission
        # is missing rather than a generic failure.
        reason = str(exc)
        if reason in {
            "support_mailbox_read_scope_not_granted",
            "support_mailbox_send_scope_not_granted",
        }:
            return _settings_redirect(error=reason)
        log.error("support_mailbox_store_failed: %s", exc)
        return _settings_redirect(error="support_mailbox_store_failed")
    except Exception as exc:
        log.error("support_mailbox_store_failed: %s", exc)
        return _settings_redirect(error="support_mailbox_store_failed")

    return _settings_redirect(connected="support_mailbox")


@router.get("/integrations/support-mailbox/status")
async def support_mailbox_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)
    return await support_mailbox.get_status(org_id=org_id)


@router.post("/integrations/support-mailbox/poll")
async def support_mailbox_poll_now(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Force a poll instead of waiting for the 2-minute cron — the difference
    between 'is this working?' and 'I'll check back later' when an admin has
    just connected the mailbox."""
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)
    try:
        return await support_mailbox.poll_one(org_id=org_id)
    except PermissionError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Gmail rejected the stored credentials ({exc}). Reconnect the mailbox.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Poll failed: {exc}") from exc


@router.delete("/integrations/support-mailbox", status_code=status.HTTP_204_NO_CONTENT)
async def support_mailbox_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_user(current_user)
    await _require_admin(user_id=user_id, token=token)
    await support_mailbox.disconnect(org_id=org_id)
