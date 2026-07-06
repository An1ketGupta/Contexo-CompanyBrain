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
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_user_client
from app.errors import NoOrganization
from app.services.integrations import drive, email_forward, notion, slack_inbound

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
    org_id, user_id, token = _require_org(current_user)
    settings = get_settings()
    client = get_user_client(token)

    # The four post-Slack providers live in the unified `integrations` table
    # (migration 036). We fetch them in the same parallel batch so the status
    # endpoint stays a single round trip from the UI.
    from app.services.integrations import _unified as _v2

    (
        drive_row,
        notion_row,
        slack_row,
        slack_subs,
        gmail_row,
        inbound,
        onedrive_row,
        confluence_row,
        github_row,
        dropbox_row,
        zoom_row,
    ) = await asyncio.gather(
        asyncio.to_thread(
            lambda: client.table("drive_integrations")
            .select("folder_ids, last_synced_at, created_at, scopes")
            .maybe_single().execute()
        ),
        asyncio.to_thread(
            lambda: client.table("notion_integrations")
            .select("workspace_name, selected_pages, last_synced_at, created_at")
            .maybe_single().execute()
        ),
        asyncio.to_thread(
            lambda: client.table("slack_integrations")
            .select("slack_team_name, installed_at, scopes")
            .maybe_single().execute()
        ),
        asyncio.to_thread(
            lambda: client.table("slack_channel_subscriptions")
            .select(
                "channel_id, channel_name, is_private, last_backfilled_at, "
                "last_error, last_error_at"
            )
            .eq("org_id", org_id)
            .execute()
        ),
        asyncio.to_thread(
            lambda: client.table("gmail_integrations")
            .select("email_address, scopes, connected_at, last_used_at")
            .eq("user_id", user_id)
            .maybe_single().execute()
        ),
        email_forward.get_inbound_address(org_id=org_id),
        _v2.get_row(org_id=org_id, provider="onedrive"),
        _v2.get_row(org_id=org_id, provider="confluence"),
        _v2.get_row(org_id=org_id, provider="github"),
        _v2.get_row(org_id=org_id, provider="dropbox"),
        _v2.get_row(org_id=org_id, provider="zoom"),
    )

    gmail_data = (gmail_row.data or {}) if gmail_row else {}
    gmail_scopes = gmail_data.get("scopes") or []
    gmail_send_scope = "https://www.googleapis.com/auth/gmail.send"

    drive_scopes = (drive_row.data or {}).get("scopes") or [] if drive_row else []

    return {
        "drive": {
            "available": bool(settings.google_client_id),
            "connected": bool(drive_row and drive_row.data),
            "folder_ids": (drive_row.data or {}).get("folder_ids", []) if drive_row else [],
            "last_synced_at": (drive_row.data or {}).get("last_synced_at") if drive_row else None,
            # Agent Day 4 — UI shows a "Reconnect to enable Docs export" banner
            # when this is False on a connected install.
            "has_docs_write_scope": drive.has_docs_write_scope(drive_scopes),
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
            "scopes_complete": slack_inbound.has_inbound_scopes(
                (slack_row.data or {}).get("scopes") if slack_row else None
            ),
            "has_canvas_scope": slack_inbound.has_canvas_scope(
                (slack_row.data or {}).get("scopes") if slack_row else None
            ),
            "required_scopes": slack_inbound.required_inbound_scopes(),
            "subscriptions": (slack_subs.data or []) if slack_subs else [],
        },
        "gmail": {
            "available": bool(settings.google_client_id),
            "connected": bool(gmail_row and gmail_row.data),
            "has_send_scope": gmail_send_scope in gmail_scopes,
            "email_address": gmail_data.get("email_address"),
            "connected_at": gmail_data.get("connected_at"),
            "last_used_at": gmail_data.get("last_used_at"),
        },
        # ── Unified integrations (migration 036) ──
        # Status shape matches drive/notion's so the frontend Card components
        # can pattern-match on .connected / .last_synced_at / .last_error.
        "onedrive": _v2_summary(
            onedrive_row, available=bool(settings.microsoft_client_id)
        ),
        "confluence": _v2_summary(
            confluence_row, available=bool(settings.atlassian_client_id)
        ),
        "github": _v2_summary(
            github_row,
            available=bool(settings.github_app_id and settings.github_app_slug),
        ),
        "dropbox": _v2_summary(
            dropbox_row, available=bool(settings.dropbox_client_id)
        ),
        "zoom": _v2_summary(
            zoom_row, available=bool(settings.zoom_client_id)
        ),
    }


def _v2_summary(row: dict[str, Any] | None, *, available: bool) -> dict[str, Any]:
    """Strip token material from a unified-table row before sending to the UI."""
    if not row:
        return {"available": available, "connected": False}
    return {
        "available": available,
        "connected": True,
        "metadata": row.get("metadata") or {},
        "resources": row.get("resources") or [],
        "last_synced_at": row.get("last_synced_at"),
        "last_error": row.get("last_error"),
        "last_error_at": row.get("last_error_at"),
        "scopes": row.get("scopes") or [],
    }


# ── Google Drive ────────────────────────────────────────────────────────────

@router.get("/integrations/drive/connect")
async def drive_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
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


@router.get("/integrations/drive/folders/browse")
async def drive_browse_folders(
    q: str | None = Query(default=None, max_length=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Search the connected Drive for folders by name — drives the picker UI."""
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    folders = await drive.list_folders(org_id=org_id, query=q)
    return {"folders": folders}


@router.get("/integrations/drive/picker-token")
async def drive_picker_token(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Hand the (refreshed) Drive access token to the browser so it can launch
    Google's native Picker. Admin-only because picking folders is an admin
    action; the token's scope is drive.readonly + documents + drive.file, so
    blast radius is limited even if it leaks to the page.
    """
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    creds = await drive.get_org_credentials(org_id)
    if not creds:
        raise HTTPException(status_code=400, detail="drive_not_connected")
    return {"access_token": creds["access_token"]}


@router.get("/integrations/drive/folders/names")
async def drive_folder_names(
    ids: str = Query(..., min_length=1, max_length=2000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Bulk-resolve friendly names for already-configured folder IDs."""
    org_id, _, _ = _require_org(current_user)
    folder_ids = [fid for fid in (ids.split(",")) if fid.strip()]
    names = await drive.get_folder_names(org_id=org_id, folder_ids=folder_ids[:50])
    return {"names": names}


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
async def notion_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
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


# ── Outbound writes: Notion create page (Agent Day 4) ───────────────────────


@router.get("/integrations/notion/write-targets")
async def notion_list_write_targets(
    q: str | None = Query(default=None),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Pages the bot can use as a parent for new pages.

    Any org member can list — picking a destination is a chat-time
    affordance, not an admin one. The bot itself has already been gated
    on the parent-page level by the Notion sharing model.
    """
    org_id, _, _ = _require_org(current_user)
    pages = await notion.list_write_targets(org_id=org_id, query=q)
    return {"pages": pages}


class CreateNotionPageBody(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    parent_page_id: str = Field(..., min_length=1, max_length=64)
    parent_page_title: str | None = Field(default=None, max_length=200)
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=200_000)
    acknowledged_warnings: bool = Field(default=False)


@router.post("/integrations/notion/create-page")
async def notion_create_page(
    body: CreateNotionPageBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    import uuid
    from datetime import datetime

    import inngest as _inngest_pkg

    from app.database import get_service_client as _svc
    from app.inngest.client import get_inngest_client

    svc = _svc()

    # Same idempotency rails as Gmail/Slack: confirm the message exists in
    # this org and hasn't already been delivered. Conversation_id is pulled
    # so the failure notification deep-links back to the source chat.
    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, conversation_id, delivery_status")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    existing = msg.data.get("delivery_status") or {}
    if existing.get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")
    conversation_id: str | None = msg.data.get("conversation_id")

    connected = await asyncio.to_thread(
        lambda: svc.table("notion_integrations")
        .select("id")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not connected or not connected.data:
        raise HTTPException(status_code=400, detail="notion_not_connected")

    from app.services.outbound_gate import (
        OutboundGateError,
        enforce_outbound_write_guards,
    )

    try:
        await enforce_outbound_write_guards(
            channel="notion",
            org_id=org_id,
            user_id=user_id,
            message_id=body.message_id,
            content=body.content,
            competitor_acknowledged=body.acknowledged_warnings,
        )
    except OutboundGateError as gate_err:
        headers: dict[str, str] = {}
        retry = gate_err.extra.get("retry_after")
        if isinstance(retry, int) and retry > 0:
            headers["Retry-After"] = str(retry)
        raise HTTPException(
            status_code=gate_err.status_code,
            detail=gate_err.code,
            headers=headers or None,
        )

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(UTC).isoformat()
    parent_title = body.parent_page_title or "Notion"

    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "notion",
                "status": "queued",
                "destination": parent_title,
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", body.message_id)
        .execute()
    )

    from app.services.outbound_audit import record_queued

    await record_queued(
        run_id=job_id,
        channel="notion",
        org_id=org_id,
        user_id=user_id,
        message_id=body.message_id,
        destination=parent_title,
        input_payload={
            "parent_page_id": body.parent_page_id,
            "parent_page_title": parent_title,
            "title": body.title,
            "content": body.content,
        },
    )

    client = get_inngest_client()
    await client.send(
        _inngest_pkg.Event(
            name="notion/create-page",
            data={
                "job_id": job_id,
                "message_id": body.message_id,
                "org_id": org_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "parent_page_id": body.parent_page_id,
                "parent_page_title": parent_title,
                "title": body.title,
                "content": body.content,
            },
            id=f"notion-create-{job_id}",
        )
    )
    return {"queued": True, "job_id": job_id}


# ── Outbound writes: Google Docs export (Agent Day 4) ───────────────────────


class CreateGoogleDocBody(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=200_000)
    # When true, the doc is shared back to the requesting user as writer
    # so it lands in their "Shared with me" without an extra step.
    share_with_me: bool = Field(default=True)
    acknowledged_warnings: bool = Field(default=False)


@router.post("/integrations/gdocs/create-doc")
async def gdocs_create_doc(
    body: CreateGoogleDocBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, _ = _require_org(current_user)

    import uuid
    from datetime import datetime

    import inngest as _inngest_pkg

    from app.database import get_service_client as _svc
    from app.inngest.client import get_inngest_client

    svc = _svc()

    # Drive must be connected AND carry the docs-write scope.
    integ = await asyncio.to_thread(
        lambda: svc.table("drive_integrations")
        .select("id, scopes")
        .eq("org_id", org_id)
        .maybe_single()
        .execute()
    )
    if not integ or not integ.data:
        raise HTTPException(status_code=400, detail="drive_not_connected")
    if not drive.has_docs_write_scope(integ.data.get("scopes") or []):
        # Same shape as gmail_send_scope_missing — UI pattern-matches on this.
        raise HTTPException(status_code=403, detail="docs_write_scope_missing")

    msg = await asyncio.to_thread(
        lambda: svc.table("messages")
        .select("id, org_id, conversation_id, delivery_status")
        .eq("id", body.message_id)
        .maybe_single()
        .execute()
    )
    if not msg or not msg.data or msg.data.get("org_id") != org_id:
        raise HTTPException(status_code=404, detail="message_not_found")
    existing = msg.data.get("delivery_status") or {}
    if existing.get("status") == "sent":
        raise HTTPException(status_code=409, detail="message_already_sent")
    conversation_id: str | None = msg.data.get("conversation_id")

    from app.services.outbound_gate import (
        OutboundGateError,
        enforce_outbound_write_guards,
    )

    try:
        await enforce_outbound_write_guards(
            channel="gdocs",
            org_id=org_id,
            user_id=user_id,
            message_id=body.message_id,
            content=body.content,
            competitor_acknowledged=body.acknowledged_warnings,
        )
    except OutboundGateError as gate_err:
        headers: dict[str, str] = {}
        retry = gate_err.extra.get("retry_after")
        if isinstance(retry, int) and retry > 0:
            headers["Retry-After"] = str(retry)
        raise HTTPException(
            status_code=gate_err.status_code,
            detail=gate_err.code,
            headers=headers or None,
        )

    # Resolve the requesting user's email server-side (don't trust the JWT
    # claim — it can be stale and we want the canonical address).
    share_with_email: str | None = None
    if body.share_with_me:
        try:
            au = await asyncio.to_thread(lambda: svc.auth.admin.get_user_by_id(user_id))
            share_with_email = getattr(getattr(au, "user", None), "email", None)
        except Exception:
            share_with_email = None

    job_id = str(uuid.uuid4())
    queued_at = datetime.now(UTC).isoformat()

    await asyncio.to_thread(
        lambda: svc.table("messages")
        .update({
            "delivery_status": {
                "channel": "gdocs",
                "status": "queued",
                "destination": "Google Docs",
                "job_id": job_id,
                "queued_at": queued_at,
            }
        })
        .eq("id", body.message_id)
        .execute()
    )

    from app.services.outbound_audit import record_queued

    await record_queued(
        run_id=job_id,
        channel="gdocs",
        org_id=org_id,
        user_id=user_id,
        message_id=body.message_id,
        destination="Google Docs",
        input_payload={
            "title": body.title,
            "share_with_email": share_with_email,
            "content": body.content,
        },
    )

    client = get_inngest_client()
    await client.send(
        _inngest_pkg.Event(
            name="gdocs/create-doc",
            data={
                "job_id": job_id,
                "message_id": body.message_id,
                "org_id": org_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "title": body.title,
                "content": body.content,
                "share_with_email": share_with_email,
            },
            id=f"gdocs-create-{job_id}",
        )
    )
    return {"queued": True, "job_id": job_id}


# ── Internal helpers ────────────────────────────────────────────────────────

def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)
