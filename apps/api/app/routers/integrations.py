"""Integrations router (Day 14): Drive + Notion + Email Forward.

Public surface:
    * GET  /integrations/status                — connected/not for each
    * GET  /integrations/drive/connect         — returns Google consent URL
    * GET  /integrations/drive/callback        — OAuth code exchange
    * POST /integrations/drive/folders         — add a folder
    * DEL  /integrations/drive/folders/{id}    — remove
    * POST /integrations/drive/sync            — manual sync trigger
    * DEL  /integrations/drive                 — disconnect
    * GET  /integrations/notion/connect        — returns Notion consent URL
    * GET  /integrations/notion/callback       — OAuth code exchange
    * GET  /integrations/notion/pages          — search accessible pages
    * POST /integrations/notion/pages          — replace selected page set
    * POST /integrations/notion/sync           — manual sync trigger
    * DEL  /integrations/notion                — disconnect
    * GET  /integrations/email/address         — get the org's inbound addr
    * POST /integrations/email/address         — provision lazily
    * POST /webhooks/email-inbound             — Resend inbound webhook

OAuth state is a short-lived HMAC token over (user_id, org_id) — distinct
from any session JWT so an attacker can't replay it later, and keyed off the
config's `oauth_state_secret` so rotation is one env-var swap.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.database import get_user_client
from app.errors import NoOrganization
from app.services.integrations import drive, email_forward, notion

log = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])

_STATE_TTL_SECONDS = 600  # 10-minute OAuth round-trip window


# ── State token (CSRF + tenant binding) ─────────────────────────────────────

def _mint_state(*, user_id: str, org_id: str, provider: str) -> str:
    """Compact HMAC token: base64(provider.user.org.ts).base64(sig).

    Why not JWT: we don't need claims tooling, and a tiny custom token avoids
    pulling jose into our deps. The HMAC is computed over the dotted form so
    the verifier can recover fields without parsing.
    """
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"{provider}.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str, *, provider: str) -> tuple[str, str]:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    try:
        prov, user_id, org_id, ts, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed state.") from exc
    if prov != provider:
        raise HTTPException(status_code=400, detail="State provider mismatch.")
    payload = f"{prov}.{user_id}.{org_id}.{ts}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid state signature.")
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired — retry.")
    return user_id, org_id


# ── Auth helpers ───────────────────────────────────────────────────────────

def _require_org(current_user: dict) -> tuple[str, str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    token = current_user.get("token")
    if not org_id or not user_id or not token:
        raise NoOrganization("No organization found. Please sign out and sign back in.")
    return org_id, user_id, token


async def _require_admin(user_id: str, token: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users").select("role").eq("id", user_id).maybe_single().execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace admins can manage integrations.",
        )


# ── Status (UI uses this to render which cards say "Connect" vs "Connected") ─

@router.get("/integrations/status")
async def integrations_status(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _, token = _require_org(current_user)
    settings = get_settings()
    client = get_user_client(token)

    drive_row, notion_row, slack_row, inbound = await asyncio.gather(
        asyncio.to_thread(
            lambda: client.table("drive_integrations")
            .select("folder_ids, last_synced_at, created_at")
            .maybe_single().execute()
        ),
        asyncio.to_thread(
            lambda: client.table("notion_integrations")
            .select("workspace_name, selected_pages, last_synced_at, created_at")
            .maybe_single().execute()
        ),
        asyncio.to_thread(
            lambda: client.table("slack_integrations")
            .select("slack_team_name, installed_at")
            .maybe_single().execute()
        ),
        email_forward.get_inbound_address(org_id=org_id),
    )

    return {
        "drive": {
            "available": bool(settings.google_client_id),
            "connected": bool(drive_row and drive_row.data),
            "folder_ids": (drive_row.data or {}).get("folder_ids", []) if drive_row else [],
            "last_synced_at": (drive_row.data or {}).get("last_synced_at") if drive_row else None,
        },
        "notion": {
            "available": bool(settings.notion_client_id),
            "connected": bool(notion_row and notion_row.data),
            "workspace_name": (notion_row.data or {}).get("workspace_name") if notion_row else None,
            "selected_pages": (notion_row.data or {}).get("selected_pages") or [] if notion_row else [],
            "last_synced_at": (notion_row.data or {}).get("last_synced_at") if notion_row else None,
        },
        "email": {
            "available": bool(settings.inbound_email_domain),
            "address": inbound,
        },
        "slack": {
            "available": bool(settings.slack_client_id),
            "connected": bool(slack_row and slack_row.data),
            "workspace_name": (slack_row.data or {}).get("slack_team_name") if slack_row else None,
            "installed_at": (slack_row.data or {}).get("installed_at") if slack_row else None,
        },
    }


# ── Google Drive ────────────────────────────────────────────────────────────

@router.get("/integrations/drive/connect")
async def drive_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google Drive integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="drive")
    return {"auth_url": drive.build_auth_url(state=state)}


@router.get("/integrations/drive/callback")
async def drive_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Notion/Drive callbacks land here from the browser, with the verified
    state binding the install to the original org. We exchange the code,
    persist tokens, then bounce back to the integrations settings page."""
    user_id, org_id = _parse_state(state, provider="drive")
    try:
        payload = await drive.exchange_code(code=code)
    except Exception as exc:
        log.error("drive_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="drive_oauth_failed")
    try:
        await drive.store_credentials(org_id=org_id, user_id=user_id, token_payload=payload)
    except Exception as exc:
        log.error("drive_store_credentials_failed: %s", exc)
        return _settings_redirect(error="drive_store_failed")
    return _settings_redirect(connected="drive")


class AddFolderBody(BaseModel):
    folder_id: str = Field(..., min_length=1, max_length=120)
    folder_name: str | None = Field(default=None, max_length=200)


@router.post("/integrations/drive/folders")
async def drive_add_folder(
    body: AddFolderBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    folders = await drive.add_folder(org_id=org_id, folder_id=body.folder_id, folder_name=body.folder_name)
    return {"folder_ids": folders}


@router.delete("/integrations/drive/folders/{folder_id}")
async def drive_remove_folder(
    folder_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    folders = await drive.remove_folder(org_id=org_id, folder_id=folder_id)
    return {"folder_ids": folders}


@router.post("/integrations/drive/sync", status_code=status.HTTP_202_ACCEPTED)
async def drive_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    result = await drive.sync_org(org_id=org_id)
    return result


@router.delete("/integrations/drive", status_code=status.HTTP_204_NO_CONTENT)
async def drive_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await drive.disconnect(org_id=org_id)


# ── Notion ──────────────────────────────────────────────────────────────────

@router.get("/integrations/notion/connect")
async def notion_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.notion_client_id:
        raise HTTPException(status_code=503, detail="Notion integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="notion")
    return {"auth_url": notion.build_auth_url(state=state)}


@router.get("/integrations/notion/callback")
async def notion_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id, org_id = _parse_state(state, provider="notion")
    try:
        payload = await notion.exchange_code(code=code)
    except Exception as exc:
        log.error("notion_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="notion_oauth_failed")
    try:
        await notion.store_credentials(org_id=org_id, user_id=user_id, token_payload=payload)
    except Exception as exc:
        log.error("notion_store_credentials_failed: %s", exc)
        return _settings_redirect(error="notion_store_failed")
    return _settings_redirect(connected="notion")


@router.get("/integrations/notion/pages")
async def notion_list_pages(
    q: str | None = None,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    pages = await notion.list_accessible_pages(org_id=org_id, query=q)
    return {"pages": pages}


class SelectPagesBody(BaseModel):
    pages: list[dict[str, str]] = Field(default_factory=list)


@router.post("/integrations/notion/pages")
async def notion_select_pages(
    body: SelectPagesBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    selected = await notion.update_selected_pages(org_id=org_id, pages=body.pages)
    return {"selected_pages": selected}


@router.post("/integrations/notion/sync", status_code=status.HTTP_202_ACCEPTED)
async def notion_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    return await notion.sync_org(org_id=org_id)


@router.delete("/integrations/notion", status_code=status.HTTP_204_NO_CONTENT)
async def notion_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await notion.disconnect(org_id=org_id)


# ── Email Forward ───────────────────────────────────────────────────────────

@router.get("/integrations/email/address")
async def email_get_address(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, _, _ = _require_org(current_user)
    address = await email_forward.get_inbound_address(org_id=org_id)
    return {"address": address}


@router.post("/integrations/email/address")
async def email_provision_address(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    address = await email_forward.ensure_inbound_address(org_id=org_id)
    return {"address": address}


@router.post("/webhooks/email-inbound", include_in_schema=False)
async def email_inbound_webhook(
    request: Request,
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    svix_signature: str | None = Header(default=None, alias="svix-signature"),
) -> dict[str, Any]:
    """Resend Inbound (or compatible) → JSON envelope of a forwarded email.

    Auth: HMAC-SHA256 of the raw body against INBOUND_EMAIL_WEBHOOK_SECRET.
    We accept either header name so a single deployment can use Resend Inbound
    (svix-signature) or a custom forwarder (X-Signature).
    """
    raw = await request.body()
    sig = x_signature or svix_signature
    if not email_forward.verify_signature(raw_body=raw, signature=sig):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Body must be valid JSON.")

    envelope = email_forward.parse_envelope(payload)
    return await email_forward.ingest_email(envelope)


# ── Internal helpers ────────────────────────────────────────────────────────

def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)
