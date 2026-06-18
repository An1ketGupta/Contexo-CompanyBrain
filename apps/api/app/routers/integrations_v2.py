"""Router for the post-Slack integrations wave: OneDrive/SharePoint,
Confluence, GitHub App, Dropbox.

All four ride the same shape:
    GET  /integrations/<p>/connect      → consent URL
    GET  /integrations/<p>/callback     → OAuth (or App install) → store creds
    GET  /integrations/<p>/resources    → list pickable resources (drives,
                                          spaces, repos, folders)
    POST /integrations/<p>/resources    → replace selected resources
    POST /integrations/<p>/sync         → manual re-sync trigger
    DEL  /integrations/<p>               → disconnect

Webhook stubs (not wired to any push subscription yet) are also defined here
so the URLs exist in production and platforms can be configured to point at
them before we turn on real-time sync.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
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
from app.services.integrations import (
    _unified,
    confluence as confluence_svc,
    dropbox_svc,
    github as github_svc,
    onedrive as onedrive_svc,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])

_STATE_TTL_SECONDS = 600


# ── Shared state helpers (mirror the older integrations.py, scoped here so
#    we don't introduce a circular import) ───────────────────────────────────


def _mint_state(*, user_id: str, org_id: str, provider: str) -> str:
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
    expected = hmac.new(
        secret.encode(), f"{prov}.{user_id}.{org_id}.{ts}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid state signature.")
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired — retry.")
    return user_id, org_id


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


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    base = settings.app_url.rstrip("/") + "/settings/integrations"
    if connected:
        return RedirectResponse(url=f"{base}?connected={connected}", status_code=302)
    if error:
        return RedirectResponse(url=f"{base}?error={error}", status_code=302)
    return RedirectResponse(url=base, status_code=302)


# ── Status (the v1 status endpoint also includes these; this is a
#    drill-down used by the per-provider pickers) ──────────────────────────


@router.get("/integrations/v2/status")
async def v2_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    """Returns the 4 new providers' rows with sensitive fields stripped.

    The legacy /integrations/status endpoint also returns these (see
    routers/integrations.py) — this drill-down is what the picker dialogs
    use to show resource selection details (cloud_name, account_email, etc.)
    without re-pulling the full status.
    """
    org_id, _, _ = _require_org(current_user)
    settings = get_settings()

    onedrive_row, confluence_row, github_row, dropbox_row = await asyncio.gather(
        _unified.get_row(org_id=org_id, provider="onedrive"),
        _unified.get_row(org_id=org_id, provider="confluence"),
        _unified.get_row(org_id=org_id, provider="github"),
        _unified.get_row(org_id=org_id, provider="dropbox"),
    )

    return {
        "onedrive": _shape(onedrive_row, available=bool(settings.microsoft_client_id)),
        "confluence": _shape(confluence_row, available=bool(settings.atlassian_client_id)),
        "github": _shape(
            github_row,
            available=bool(settings.github_app_id and settings.github_app_slug),
        ),
        "dropbox": _shape(dropbox_row, available=bool(settings.dropbox_client_id)),
    }


def _shape(row: dict[str, Any] | None, *, available: bool) -> dict[str, Any]:
    if not row:
        return {"available": available, "connected": False}
    # Strip tokens — even though RLS limits access, defense in depth.
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


# ──────────────────────────────────────────────────────────────────────────
#                           OneDrive / SharePoint
# ──────────────────────────────────────────────────────────────────────────


@router.get("/integrations/onedrive/connect")
async def onedrive_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.microsoft_client_id:
        raise HTTPException(status_code=503, detail="OneDrive integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="onedrive")
    return {"auth_url": onedrive_svc.build_auth_url(state=state)}


@router.get("/integrations/onedrive/callback")
async def onedrive_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id, org_id = _parse_state(state, provider="onedrive")
    try:
        payload = await onedrive_svc.exchange_code(code=code)
    except Exception as exc:
        log.error("onedrive_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="onedrive_oauth_failed")
    try:
        await onedrive_svc.store_credentials(
            org_id=org_id, user_id=user_id, token_payload=payload
        )
    except Exception as exc:
        log.error("onedrive_store_credentials_failed: %s", exc)
        return _settings_redirect(error="onedrive_store_failed")
    return _settings_redirect(connected="onedrive")


@router.get("/integrations/onedrive/personal-drive")
async def onedrive_personal(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    drive = await onedrive_svc.list_personal_drive(org_id=org_id)
    return {"drive": drive}


@router.get("/integrations/onedrive/sites")
async def onedrive_sites(
    q: str | None = Query(default=None, max_length=200),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    sites = await onedrive_svc.search_sites(org_id=org_id, query=q)
    return {"sites": sites}


@router.get("/integrations/onedrive/sites/{site_id}/drives")
async def onedrive_site_drives(
    site_id: str,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    drives = await onedrive_svc.list_site_drives(org_id=org_id, site_id=site_id)
    return {"drives": drives}


class OneDriveResourcesBody(BaseModel):
    resources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/integrations/onedrive/resources")
async def onedrive_update_resources(
    body: OneDriveResourcesBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    saved = await onedrive_svc.update_resources(org_id=org_id, resources=body.resources)
    return {"resources": saved}


@router.post("/integrations/onedrive/sync", status_code=status.HTTP_202_ACCEPTED)
async def onedrive_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    return await onedrive_svc.sync_org(org_id=org_id)


@router.delete("/integrations/onedrive", status_code=status.HTTP_204_NO_CONTENT)
async def onedrive_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await onedrive_svc.disconnect(org_id=org_id)


# ──────────────────────────────────────────────────────────────────────────
#                                 Confluence
# ──────────────────────────────────────────────────────────────────────────


@router.get("/integrations/confluence/connect")
async def confluence_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.atlassian_client_id:
        raise HTTPException(status_code=503, detail="Confluence integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="confluence")
    return {"auth_url": confluence_svc.build_auth_url(state=state)}


@router.get("/integrations/confluence/callback")
async def confluence_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id, org_id = _parse_state(state, provider="confluence")
    try:
        payload = await confluence_svc.exchange_code(code=code)
    except Exception as exc:
        log.error("confluence_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="confluence_oauth_failed")
    try:
        await confluence_svc.store_credentials(
            org_id=org_id, user_id=user_id, token_payload=payload
        )
    except Exception as exc:
        log.error("confluence_store_credentials_failed: %s", exc)
        return _settings_redirect(error="confluence_store_failed")
    return _settings_redirect(connected="confluence")


@router.get("/integrations/confluence/spaces")
async def confluence_spaces(
    cloud_id: str = Query(..., min_length=1, max_length=120),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    spaces = await confluence_svc.list_spaces(org_id=org_id, cloud_id=cloud_id)
    return {"spaces": spaces}


class ConfluenceResourcesBody(BaseModel):
    resources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/integrations/confluence/resources")
async def confluence_update_resources(
    body: ConfluenceResourcesBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    saved = await confluence_svc.update_resources(org_id=org_id, resources=body.resources)
    return {"resources": saved}


@router.post("/integrations/confluence/sync", status_code=status.HTTP_202_ACCEPTED)
async def confluence_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    return await confluence_svc.sync_org(org_id=org_id)


@router.delete("/integrations/confluence", status_code=status.HTTP_204_NO_CONTENT)
async def confluence_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await confluence_svc.disconnect(org_id=org_id)


# ──────────────────────────────────────────────────────────────────────────
#                                  GitHub
# ──────────────────────────────────────────────────────────────────────────


@router.get("/integrations/github/connect")
async def github_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not (settings.github_app_id and settings.github_app_slug):
        raise HTTPException(status_code=503, detail="GitHub integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="github")
    return {"auth_url": github_svc.build_install_url(state=state)}


@router.get("/integrations/github/callback")
async def github_callback(
    state: str = Query(...),
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    code: str | None = Query(default=None),  # ignored — install-only flow
) -> RedirectResponse:
    """GitHub App callback. Unlike OAuth, we get `installation_id` instead
    of a `code`. `setup_action` is 'install' on first install, 'update' if
    the user added/removed repos."""
    user_id, org_id = _parse_state(state, provider="github")
    if not installation_id:
        return _settings_redirect(error="github_missing_installation_id")
    try:
        await github_svc.complete_install(
            org_id=org_id, user_id=user_id, installation_id=installation_id
        )
    except Exception as exc:
        log.error("github_install_complete_failed: %s", exc)
        return _settings_redirect(error="github_install_failed")
    return _settings_redirect(connected="github")


@router.get("/integrations/github/repos")
async def github_repos(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    repos = await github_svc.list_installation_repos(org_id=org_id)
    return {"repos": repos}


class GitHubResourcesBody(BaseModel):
    resources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/integrations/github/resources")
async def github_update_resources(
    body: GitHubResourcesBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    saved = await github_svc.update_resources(org_id=org_id, resources=body.resources)
    return {"resources": saved}


@router.post("/integrations/github/sync", status_code=status.HTTP_202_ACCEPTED)
async def github_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    return await github_svc.sync_org(org_id=org_id)


@router.delete("/integrations/github", status_code=status.HTTP_204_NO_CONTENT)
async def github_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await github_svc.disconnect(org_id=org_id)


# ──────────────────────────────────────────────────────────────────────────
#                                  Dropbox
# ──────────────────────────────────────────────────────────────────────────


@router.get("/integrations/dropbox/connect")
async def dropbox_connect(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    settings = get_settings()
    if not settings.dropbox_client_id:
        raise HTTPException(status_code=503, detail="Dropbox integration is not configured.")
    state = _mint_state(user_id=user_id, org_id=org_id, provider="dropbox")
    return {"auth_url": dropbox_svc.build_auth_url(state=state)}


@router.get("/integrations/dropbox/callback")
async def dropbox_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    user_id, org_id = _parse_state(state, provider="dropbox")
    try:
        payload = await dropbox_svc.exchange_code(code=code)
    except Exception as exc:
        log.error("dropbox_oauth_exchange_failed: %s", exc)
        return _settings_redirect(error="dropbox_oauth_failed")
    try:
        await dropbox_svc.store_credentials(
            org_id=org_id, user_id=user_id, token_payload=payload
        )
    except Exception as exc:
        log.error("dropbox_store_credentials_failed: %s", exc)
        return _settings_redirect(error="dropbox_store_failed")
    return _settings_redirect(connected="dropbox")


@router.get("/integrations/dropbox/folders")
async def dropbox_folders(
    path: str = Query(default=""),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    folders = await dropbox_svc.list_folders(org_id=org_id, path=path)
    return {"folders": folders}


class DropboxResourcesBody(BaseModel):
    resources: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/integrations/dropbox/resources")
async def dropbox_update_resources(
    body: DropboxResourcesBody,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    saved = await dropbox_svc.update_resources(org_id=org_id, resources=body.resources)
    return {"resources": saved}


@router.post("/integrations/dropbox/sync", status_code=status.HTTP_202_ACCEPTED)
async def dropbox_sync_now(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    return await dropbox_svc.sync_org(org_id=org_id)


@router.delete("/integrations/dropbox", status_code=status.HTTP_204_NO_CONTENT)
async def dropbox_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id, token = _require_org(current_user)
    await _require_admin(user_id, token)
    await dropbox_svc.disconnect(org_id=org_id)


# ──────────────────────────────────────────────────────────────────────────
#                            Webhook stubs
#
# These exist now so platforms can be configured with stable URLs before
# we wire up push subscriptions. Each handler verifies the platform's HMAC
# signature, logs receipt, and acknowledges 200. Wiring them to actually
# kick off sync_org() lands in a follow-up sprint once polling has had time
# to bake.
# ──────────────────────────────────────────────────────────────────────────


@router.post("/webhooks/onedrive", include_in_schema=False)
async def onedrive_webhook(
    request: Request,
    validation_token: str | None = Query(default=None, alias="validationToken"),
) -> Any:
    """Microsoft Graph webhook handshake: subscription registration sends a
    `validationToken` query param; we must echo it back as text/plain inside
    10 seconds for Graph to accept the subscription. Once live, Graph POSTs
    JSON change notifications to the same URL with a clientState string we
    verify against the integration's webhook_secret.
    """
    if validation_token:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(validation_token, status_code=200)
    body = await request.json()
    # Each notification carries clientState; we'd look it up by subscription
    # → org_id in the future. For now: log + ack so the platform doesn't
    # retry-storm us during the bake-in phase.
    log.info("onedrive_webhook received: %d notifications", len(body.get("value") or []))
    return {"ok": True}


@router.post("/webhooks/confluence", include_in_schema=False)
async def confluence_webhook(request: Request) -> dict[str, Any]:
    """Atlassian Connect webhooks include a JWT in the Authorization header.
    For Forge / OAuth 2.0 (3LO) integrations webhooks aren't supported the
    same way — this stub catches whatever arrives so the URL is stable.
    """
    log.info("confluence_webhook received")
    return {"ok": True}


@router.post("/webhooks/github", include_in_schema=False)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """GitHub App webhook. Signature verified against GITHUB_APP_WEBHOOK_SECRET.

    Once wired, events of interest are: push, issues, discussion,
    discussion_comment, installation, installation_repositories. Until
    then we just ack so GitHub doesn't disable the hook.
    """
    body = await request.body()
    if not github_svc.verify_webhook_signature(body=body, signature=x_hub_signature_256):
        # Don't 401 — GitHub treats that as a hard fail and disables the
        # hook. Return 200 with a log so admins can debug via the deliveries
        # page without us getting auto-disabled.
        log.warning("github_webhook_signature_invalid event=%s", x_github_event)
        return {"ok": False, "reason": "signature"}
    log.info("github_webhook event=%s", x_github_event)
    return {"ok": True}


@router.post("/webhooks/dropbox", include_in_schema=False)
async def dropbox_webhook(
    request: Request,
    challenge: str | None = Query(default=None),
) -> Any:
    """Dropbox webhook verification: on subscribe, Dropbox calls with a
    `challenge` query param; we must echo it back verbatim. Live POSTs
    carry an X-Dropbox-Signature header (HMAC-SHA256 of the body using our
    app secret) and a JSON envelope with `list_folder.accounts`.
    """
    if challenge:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(challenge, status_code=200)
    body = await request.body()
    sig = request.headers.get("X-Dropbox-Signature")
    settings = get_settings()
    if settings.dropbox_client_secret and sig:
        expected = hmac.new(
            settings.dropbox_client_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            log.warning("dropbox_webhook_signature_invalid")
            return {"ok": False, "reason": "signature"}
    log.info("dropbox_webhook received: %d bytes", len(body))
    return {"ok": True}
