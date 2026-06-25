"""Integration setup endpoints for Agent2 Day 5/6 providers.

Three groups:

  * ATS (Greenhouse / Lever / Ashby) — API-key based, admin only:
        POST /integrations/{provider}/connect      { api_key, on_behalf_of_user_id? }
        GET  /integrations/{provider}/status
        DEL  /integrations/{provider}

  * Asana — per-user OAuth:
        GET  /integrations/asana/connect
        GET  /integrations/asana/callback
        POST /integrations/asana/resources         pick workspace + project
        GET  /integrations/asana/status
        DEL  /integrations/asana

  * Linear — per-user OAuth:
        GET  /integrations/linear/connect
        GET  /integrations/linear/callback
        POST /integrations/linear/resources        pick team
        GET  /integrations/linear/status
        DEL  /integrations/linear

State-mint pattern matches gmail_router/google_workspace_router so a single
oauth_state_secret protects every flow.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth import verify_jwt
from app.config import get_settings
from app.core.rate_limiter import oauth_callback_limiter
from app.database import get_service_client, get_user_client
from app.errors import NoOrganization
from app.services.integrations import asana as asana_svc
from app.services.integrations import jira as jira_svc
from app.services.integrations import linear as linear_svc
from app.services.integrations.ats import ashby, greenhouse, lever

log = logging.getLogger(__name__)
router = APIRouter(tags=["agent2-integrations"])

_STATE_TTL_SECONDS = 600


def _require_user(current_user: dict) -> tuple[str, str]:
    org_id = current_user.get("org_id")
    user_id = current_user.get("user_id")
    if not org_id or not user_id:
        raise NoOrganization("No organization found.")
    return org_id, user_id


async def _require_admin(token: str, user_id: str) -> None:
    client = get_user_client(token)
    me = await asyncio.to_thread(
        lambda: client.table("users")
        .select("role")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not me or not me.data or me.data.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only.")


def _mint_state(*, provider: str, user_id: str, org_id: str) -> str:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    payload = f"{provider}.{user_id}.{org_id}.{int(time.time())}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _parse_state(token: str, *, expected_provider: str) -> tuple[str, str]:
    settings = get_settings()
    secret = settings.oauth_state_secret
    if not secret:
        raise HTTPException(status_code=500, detail="OAuth state secret not configured.")
    try:
        prov, user_id, org_id, ts, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed state.") from exc
    if prov != expected_provider:
        raise HTTPException(status_code=400, detail="State provider mismatch.")
    payload = f"{prov}.{user_id}.{org_id}.{ts}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid state signature.")
    if int(time.time()) - int(ts) > _STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="OAuth state expired — retry.")
    return user_id, org_id


def _settings_redirect(*, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    base = get_settings().app_url.rstrip("/") + "/settings/integrations"
    qs = f"connected={connected}" if connected else f"error={error}" if error else ""
    return RedirectResponse(url=f"{base}?{qs}" if qs else base, status_code=302)


# ╭──────────────────────────────────────────────────────────────╮
# │ ATS — Greenhouse / Lever / Ashby (API key, admin only)       │
# ╰──────────────────────────────────────────────────────────────╯


class AtsConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=10, max_length=512)
    # Greenhouse + Lever can scope writes to a specific user; Ashby uses the
    # team-membership id. All three are optional — providers default to the
    # account-owner when omitted.
    on_behalf_of_user_id: str | None = None
    # Account-level metadata captured at connect to avoid extra round-trips
    # on every publish (Greenhouse needs the board subdomain to build URLs).
    metadata: dict[str, Any] = Field(default_factory=dict)


_AtsProvider = Literal["greenhouse", "lever", "ashby"]


@router.post("/integrations/{provider}/connect")
async def ats_connect(
    provider: _AtsProvider,
    body: AtsConnectRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    """Validate the API key by making a cheap test call, then persist as an
    org-scoped row in `integrations`."""
    org_id, user_id = _require_user(current_user)
    token = current_user.get("token")
    if not token:
        raise NoOrganization("No organization found.")
    await _require_admin(token, user_id)

    tester = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}[provider]
    try:
        ok = await tester.test_connection(api_key=body.api_key)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"connection_test_failed: {exc}") from exc
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="api_key_invalid")

    svc = get_service_client()
    metadata: dict[str, Any] = dict(body.metadata or {})
    if body.on_behalf_of_user_id:
        if provider == "greenhouse":
            metadata["on_behalf_of_user_id"] = body.on_behalf_of_user_id
        elif provider == "lever":
            metadata["posting_owner_user_id"] = body.on_behalf_of_user_id
        elif provider == "ashby":
            metadata["hiring_team_member_id"] = body.on_behalf_of_user_id

    row = {
        "org_id": org_id,
        "provider": provider,
        "scope_user_id": None,
        "connected_by": user_id,
        "access_token": body.api_key,
        "refresh_token": None,
        "token_expiry": None,
        "scopes": [],
        "metadata": metadata,
    }

    def _upsert() -> dict[str, Any]:
        existing = (
            svc.table("integrations")
            .select("id")
            .eq("org_id", org_id)
            .eq("provider", provider)
            .is_("scope_user_id", None)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            svc.table("integrations").update(row).eq("id", existing.data["id"]).execute()
            return {**row, "id": existing.data["id"]}
        res = svc.table("integrations").insert(row).execute()
        return (res.data or [row])[0]

    await asyncio.to_thread(_upsert)
    return {"connected": True, "provider": provider}


@router.get("/integrations/{provider}/status")
async def ats_status(
    provider: _AtsProvider,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, _ = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("created_at, metadata, connected_by")
        .eq("org_id", org_id)
        .eq("provider", provider)
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"connected": False}
    return {
        "connected": True,
        "metadata": row.data.get("metadata") or {},
        "connected_at": row.data.get("created_at"),
        "connected_by": row.data.get("connected_by"),
    }


@router.delete("/integrations/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def ats_disconnect(
    provider: _AtsProvider,
    current_user: dict = Depends(verify_jwt),
) -> None:
    org_id, user_id = _require_user(current_user)
    token = current_user.get("token")
    if not token:
        raise NoOrganization("No organization found.")
    await _require_admin(token, user_id)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("provider", provider)
        .is_("scope_user_id", None)
        .execute()
    )


# ╭──────────────────────────────────────────────────────────────╮
# │ Asana — per-user OAuth                                        │
# ╰──────────────────────────────────────────────────────────────╯


@router.get("/integrations/asana/connect")
async def asana_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.asana_client_id:
        raise HTTPException(status_code=503, detail="Asana integration not configured.")
    state = _mint_state(provider="asana", user_id=user_id, org_id=org_id)
    params = {
        "client_id": settings.asana_client_id,
        "redirect_uri": settings.asana_oauth_redirect_uri,
        "response_type": "code",
        "scope": "default",
        "state": state,
    }
    return {"auth_url": f"https://app.asana.com/-/oauth_authorize?{urlencode(params)}"}


@router.get("/integrations/asana/callback")
async def asana_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error or not code:
        return _settings_redirect(error=f"asana_{error or 'missing_code'}")
    user_id, org_id = _parse_state(state, expected_provider="asana")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://app.asana.com/-/oauth_token",
            data={
                "grant_type": "authorization_code",
                "client_id": settings.asana_client_id,
                "client_secret": settings.asana_client_secret,
                "redirect_uri": settings.asana_oauth_redirect_uri,
                "code": code,
            },
        )
    if resp.status_code != 200:
        log.error("asana_token_exchange_failed: %s %s", resp.status_code, resp.text[:200])
        return _settings_redirect(error="asana_exchange_failed")
    payload = resp.json()
    expires_in = int(payload.get("expires_in") or 3600)
    expiry = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()

    row = {
        "org_id": org_id,
        "provider": "asana",
        "scope_user_id": user_id,
        "connected_by": user_id,
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "token_expiry": expiry,
        "scopes": [],
        "metadata": {"asana_user": payload.get("data", {})},
        "resources": {},  # filled in by /resources endpoint
    }
    svc = get_service_client()

    def _upsert() -> None:
        existing = (
            svc.table("integrations")
            .select("id, resources")
            .eq("org_id", org_id)
            .eq("provider", "asana")
            .eq("scope_user_id", user_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            # Preserve any previously-picked resources on a re-auth.
            row["resources"] = existing.data.get("resources") or {}
            svc.table("integrations").update(row).eq("id", existing.data["id"]).execute()
        else:
            svc.table("integrations").insert(row).execute()

    await asyncio.to_thread(_upsert)
    return _settings_redirect(connected="asana")


class AsanaResourcesRequest(BaseModel):
    workspace_gid: str
    default_project_gid: str | None = None


@router.post("/integrations/asana/resources")
async def asana_pick_resources(
    body: AsanaResourcesRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update(
            {
                "resources": {
                    "workspace_gid": body.workspace_gid,
                    "default_project_gid": body.default_project_gid,
                }
            }
        )
        .eq("org_id", org_id)
        .eq("provider", "asana")
        .eq("scope_user_id", user_id)
        .execute()
    )
    if not (res and res.data):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asana not connected.")
    return {"ok": True}


@router.get("/integrations/asana/workspaces")
async def asana_list_workspaces(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    return {"workspaces": await asana_svc.list_workspaces(org_id=org_id, user_id=user_id)}


@router.get("/integrations/asana/projects")
async def asana_list_projects(
    workspace_gid: str = Query(...),
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    return {
        "projects": await asana_svc.list_projects(
            org_id=org_id, user_id=user_id, workspace_gid=workspace_gid
        )
    }


@router.get("/integrations/asana/status")
async def asana_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("resources, metadata, created_at")
        .eq("org_id", org_id)
        .eq("provider", "asana")
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"connected": False}
    return {"connected": True, **(row.data or {})}


@router.delete("/integrations/asana", status_code=status.HTTP_204_NO_CONTENT)
async def asana_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("provider", "asana")
        .eq("scope_user_id", user_id)
        .execute()
    )


# ╭──────────────────────────────────────────────────────────────╮
# │ Linear — per-user OAuth                                       │
# ╰──────────────────────────────────────────────────────────────╯


@router.get("/integrations/linear/connect")
async def linear_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.linear_client_id:
        raise HTTPException(status_code=503, detail="Linear integration not configured.")
    state = _mint_state(provider="linear", user_id=user_id, org_id=org_id)
    params = {
        "client_id": settings.linear_client_id,
        "redirect_uri": settings.linear_oauth_redirect_uri,
        "response_type": "code",
        "scope": "read,write,issues:create",
        "state": state,
        "actor": "user",
    }
    return {"auth_url": f"https://linear.app/oauth/authorize?{urlencode(params)}"}


@router.get("/integrations/linear/callback")
async def linear_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error or not code:
        return _settings_redirect(error=f"linear_{error or 'missing_code'}")
    user_id, org_id = _parse_state(state, expected_provider="linear")
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://api.linear.app/oauth/token",
            data={
                "client_id": settings.linear_client_id,
                "client_secret": settings.linear_client_secret,
                "redirect_uri": settings.linear_oauth_redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
            },
        )
    if resp.status_code != 200:
        log.error("linear_token_exchange_failed: %s %s", resp.status_code, resp.text[:200])
        return _settings_redirect(error="linear_exchange_failed")
    payload = resp.json()

    row = {
        "org_id": org_id,
        "provider": "linear",
        "scope_user_id": user_id,
        "connected_by": user_id,
        "access_token": payload["access_token"],
        "refresh_token": None,  # Linear tokens are non-expiring per their docs
        "token_expiry": None,
        "scopes": (payload.get("scope") or "").split(",") if payload.get("scope") else [],
        "metadata": {},
        "resources": {},
    }
    svc = get_service_client()

    def _upsert() -> None:
        existing = (
            svc.table("integrations")
            .select("id, resources")
            .eq("org_id", org_id)
            .eq("provider", "linear")
            .eq("scope_user_id", user_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            row["resources"] = existing.data.get("resources") or {}
            svc.table("integrations").update(row).eq("id", existing.data["id"]).execute()
        else:
            svc.table("integrations").insert(row).execute()

    await asyncio.to_thread(_upsert)
    return _settings_redirect(connected="linear")


class LinearResourcesRequest(BaseModel):
    team_id: str


@router.post("/integrations/linear/resources")
async def linear_pick_resources(
    body: LinearResourcesRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"resources": {"team_id": body.team_id}})
        .eq("org_id", org_id)
        .eq("provider", "linear")
        .eq("scope_user_id", user_id)
        .execute()
    )
    if not (res and res.data):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Linear not connected.")
    return {"ok": True}


@router.get("/integrations/linear/teams")
async def linear_list_teams(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    return {"teams": await linear_svc.list_teams(org_id=org_id, user_id=user_id)}


@router.get("/integrations/linear/status")
async def linear_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("resources, metadata, created_at")
        .eq("org_id", org_id)
        .eq("provider", "linear")
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"connected": False}
    return {"connected": True, **(row.data or {})}


@router.delete("/integrations/linear", status_code=status.HTTP_204_NO_CONTENT)
async def linear_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("provider", "linear")
        .eq("scope_user_id", user_id)
        .execute()
    )


# ╭──────────────────────────────────────────────────────────────╮
# │ Jira Cloud — per-user OAuth 3LO                              │
# ╰──────────────────────────────────────────────────────────────╯
#
# Flow mirrors Linear/Asana. Differences:
#   * Atlassian requires `audience=api.atlassian.com` on the authorize URL.
#   * Refresh tokens require the `offline_access` scope explicitly.
#   * After the exchange we hit /accessible-resources to resolve the cloud_id
#     for the granted site (one cloud_id per site). For multi-site users we
#     pick the first; a future picker UI can select.

_JIRA_SCOPES = " ".join(
    [
        "read:jira-work",
        "write:jira-work",
        "read:jira-user",
        "offline_access",
    ]
)


@router.get("/integrations/jira/connect")
async def jira_connect(
    current_user: dict = Depends(verify_jwt),
    _rl: None = Depends(oauth_callback_limiter),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    settings = get_settings()
    if not settings.jira_client_id:
        raise HTTPException(status_code=503, detail="Jira integration not configured.")
    state = _mint_state(provider="jira", user_id=user_id, org_id=org_id)
    params = {
        "audience": "api.atlassian.com",
        "client_id": settings.jira_client_id,
        "scope": _JIRA_SCOPES,
        "redirect_uri": settings.jira_oauth_redirect_uri,
        "response_type": "code",
        "prompt": "consent",
        "state": state,
    }
    return {"auth_url": f"https://auth.atlassian.com/authorize?{urlencode(params)}"}


@router.get("/integrations/jira/callback")
async def jira_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error or not code:
        return _settings_redirect(error=f"jira_{error or 'missing_code'}")
    user_id, org_id = _parse_state(state, expected_provider="jira")
    settings = get_settings()

    # Token exchange
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            "https://auth.atlassian.com/oauth/token",
            json={
                "grant_type": "authorization_code",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "code": code,
                "redirect_uri": settings.jira_oauth_redirect_uri,
            },
        )
    if resp.status_code != 200:
        log.error("jira_token_exchange_failed: %s %s", resp.status_code, resp.text[:200])
        return _settings_redirect(error="jira_exchange_failed")
    payload = resp.json()

    # Resolve the user's accessible site(s). If there are multiple, we pick
    # the first — the picker UI lets them change it after.
    try:
        resources = await jira_svc.list_accessible_resources(
            access_token=payload["access_token"]
        )
    except Exception as exc:
        log.error("jira_resources_lookup_failed: %s", exc)
        return _settings_redirect(error="jira_resources_failed")
    if not resources:
        return _settings_redirect(error="jira_no_sites")
    site = resources[0]

    expiry = (
        datetime.now(UTC)
        + timedelta(seconds=int(payload.get("expires_in") or 3500))
    ).isoformat()

    row = {
        "org_id": org_id,
        "provider": "jira",
        "scope_user_id": user_id,
        "connected_by": user_id,
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "token_expiry": expiry,
        "scopes": (payload.get("scope") or "").split(" "),
        "metadata": {
            "cloud_id": site.get("id"),
            "site_url": site.get("url"),
            "site_name": site.get("name"),
        },
        "resources": {},
    }
    svc = get_service_client()

    def _upsert() -> None:
        existing = (
            svc.table("integrations")
            .select("id, resources")
            .eq("org_id", org_id)
            .eq("provider", "jira")
            .eq("scope_user_id", user_id)
            .maybe_single()
            .execute()
        )
        if existing and existing.data:
            row["resources"] = existing.data.get("resources") or {}
            svc.table("integrations").update(row).eq("id", existing.data["id"]).execute()
        else:
            svc.table("integrations").insert(row).execute()

    await asyncio.to_thread(_upsert)
    return _settings_redirect(connected="jira")


class JiraResourcesRequest(BaseModel):
    project_key: str = Field(..., min_length=1, max_length=64)
    project_id: str | None = None
    default_issue_type: str = Field(default="Task", max_length=64)


@router.post("/integrations/jira/resources")
async def jira_pick_resources(
    body: JiraResourcesRequest,
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    payload = {
        "project_key": body.project_key,
        "project_id": body.project_id,
        "default_issue_type": body.default_issue_type,
    }
    res = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"resources": payload})
        .eq("org_id", org_id)
        .eq("provider", "jira")
        .eq("scope_user_id", user_id)
        .execute()
    )
    if not (res and res.data):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jira not connected.")
    return {"ok": True}


@router.get("/integrations/jira/projects")
async def jira_list_projects(
    current_user: dict = Depends(verify_jwt),
) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    return {"projects": await jira_svc.list_projects(org_id=org_id, user_id=user_id)}


@router.get("/integrations/jira/status")
async def jira_status(current_user: dict = Depends(verify_jwt)) -> dict[str, Any]:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("resources, metadata, created_at")
        .eq("org_id", org_id)
        .eq("provider", "jira")
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return {"connected": False}
    return {"connected": True, **(row.data or {})}


@router.delete("/integrations/jira", status_code=status.HTTP_204_NO_CONTENT)
async def jira_disconnect(current_user: dict = Depends(verify_jwt)) -> None:
    org_id, user_id = _require_user(current_user)
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .delete()
        .eq("org_id", org_id)
        .eq("provider", "jira")
        .eq("scope_user_id", user_id)
        .execute()
    )
