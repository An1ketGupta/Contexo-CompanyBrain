"""Google Workspace integration router (Agent2 Day 6 — Calendar + Docs).

Per-user OAuth bundling calendar.readonly + documents + drive.file + gmail.send.
Separate from the Gmail-only flow in `gmail_router.py` because Google honors
incremental authorization only when each consent screen redirects to a
*different* state — bundling on the existing flow would silently downgrade
new connections to gmail.send-only.

Endpoints:
    GET  /integrations/google-workspace/connect    → consent URL
    GET  /integrations/google-workspace/callback   → exchange + persist
    GET  /integrations/google-workspace/status     → connected? scopes?
    DEL  /integrations/google-workspace            → disconnect

    GET  /integrations/google-workspace/meet-folders/picker-token
    POST /integrations/google-workspace/meet-folders
    DEL  /integrations/google-workspace/meet-folders/{folder_id}
    GET  /integrations/google-workspace/meet-folders/names
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
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_service_client
from app.errors import NoOrganization
from app.services.integrations import google_meet_transcripts, google_workspace

log = logging.getLogger(__name__)

router = APIRouter(tags=["google_workspace"])

_STATE_TTL_SECONDS = 600
_PROVIDER = "google_workspace"


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
    qs = f"connected={connected}" if connected else f"error={error}" if error else ""
    return RedirectResponse(url=f"{base}?{qs}" if qs else base, status_code=302)


@router.get("/integrations/google-workspace/connect")
async def connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google integration not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id)
    return {"auth_url": google_workspace.build_auth_url(state=state)}


@router.get("/integrations/google-workspace/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error:
        return _settings_redirect(error=f"gw_oauth_{error}")
    if not code:
        return _settings_redirect(error="gw_oauth_missing_code")

    user_id, org_id = _parse_state(state)
    try:
        payload = await google_workspace.exchange_code(code=code)
    except Exception as exc:
        log.error("gw_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="gw_oauth_failed")
    try:
        await google_workspace.store_credentials(
            org_id=org_id, user_id=user_id, token_payload=payload
        )
    except RuntimeError as exc:
        return _settings_redirect(error=f"gw_{exc}")
    except Exception as exc:
        log.error("gw_store_failed: %s", exc)
        return _settings_redirect(error="gw_store_failed")
    return _settings_redirect(connected="google_workspace")


@router.get("/integrations/google-workspace/status")
async def gw_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("scopes, metadata, created_at")
        .eq("org_id", org_id)
        .eq("provider", _PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"connected": False}
    scopes = row.data.get("scopes") or []
    metadata = row.data.get("metadata") or {}
    return {
        "connected": True,
        "email_address": metadata.get("email_address"),
        "has_calendar_read": "https://www.googleapis.com/auth/calendar.readonly" in scopes,
        "has_calendar_write": "https://www.googleapis.com/auth/calendar.events" in scopes,
        "has_docs": "https://www.googleapis.com/auth/documents" in scopes,
        "has_gmail_send": "https://www.googleapis.com/auth/gmail.send" in scopes,
        "meet_transcript_folder_ids": metadata.get("meet_transcript_folder_ids") or [],
        "connected_at": row.data.get("created_at"),
    }


@router.delete("/integrations/google-workspace", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    await google_workspace.disconnect(org_id=org_id, user_id=user_id)


# ── Meet-transcript folder picker ────────────────────────────────────────────


@router.get("/integrations/google-workspace/meet-folders/picker-token")
async def meet_folders_picker_token(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Hand the (refreshed) Workspace access token to the browser so it can
    launch Google's native Picker, scoped to drive.file — the Picker is
    allowed to show folder-browse UI even though the token itself can't list
    arbitrary Drive contents; picking a folder is what grants that access."""
    org_id, user_id = _require_user(current_user)
    access_token = await google_workspace.get_user_token(org_id=org_id, user_id=user_id)
    if not access_token:
        raise HTTPException(status_code=400, detail="google_workspace_not_connected")
    return {"access_token": access_token}


class AddMeetFolderBody(BaseModel):
    folder_id: str = Field(..., min_length=1, max_length=120)
    folder_name: str | None = Field(default=None, max_length=200)


@router.post("/integrations/google-workspace/meet-folders")
async def add_meet_folder(
    body: AddMeetFolderBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    try:
        folders = await google_meet_transcripts.add_folder(
            org_id=org_id, user_id=user_id, folder_id=body.folder_id, folder_name=body.folder_name
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"folder_ids": folders}


@router.delete("/integrations/google-workspace/meet-folders/{folder_id}")
async def remove_meet_folder(
    folder_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    folders = await google_meet_transcripts.remove_folder(
        org_id=org_id, user_id=user_id, folder_id=folder_id
    )
    return {"folder_ids": folders}


@router.get("/integrations/google-workspace/meet-folders/names")
async def meet_folder_names(
    ids: str = Query(..., min_length=1, max_length=2000),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    folder_ids = [fid for fid in ids.split(",") if fid.strip()]
    names = await google_meet_transcripts.get_folder_names(
        org_id=org_id, user_id=user_id, folder_ids=folder_ids[:50]
    )
    return {"names": names}
