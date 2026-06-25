"""Asana adapter (per-user OAuth).

Connect flow lives in `routers/integrations_v2.py` — this module is the
network adapter only.

Surface:
  list_workspaces(org_id, user_id)            → [{gid, name}]
  list_projects(org_id, user_id, workspace)   → [{gid, name}]
  create_task(org_id, user_id, ...)           → {task_id, url}

Storage: `integrations` table, provider='asana', scope_user_id=user_id.
The `resources` JSONB on the row stores the selected (workspace_gid,
default_project_gid). The user picks these once at connect time.

Auth header: Bearer <access_token>. Asana refresh tokens are long-lived;
refresh URL: https://app.asana.com/-/oauth_token
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)

PROVIDER = "asana"
_API = "https://app.asana.com/api/1.0"
_TOKEN_URL = "https://app.asana.com/-/oauth_token"


async def _get_row(*, org_id: str, user_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    res = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("*")
        .eq("org_id", org_id)
        .eq("provider", PROVIDER)
        .eq("scope_user_id", user_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


async def _get_access_token(*, org_id: str, user_id: str) -> str:
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("asana_not_connected")
    expiry = row.get("token_expiry")
    if expiry:
        try:
            exp = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp - timedelta(seconds=60) > datetime.now(UTC):
                return row["access_token"]
        except ValueError:
            pass
    # Refresh.
    settings = get_settings()
    refresh = row.get("refresh_token")
    if not refresh:
        raise PermissionError("asana_refresh_missing")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.asana_client_id,
                "client_secret": settings.asana_client_secret,
                "refresh_token": refresh,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"asana_refresh_failed: {resp.status_code} {resp.text[:200]}"
        )
    payload = resp.json()
    new_access = payload["access_token"]
    new_expiry = (
        datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3600))
    ).isoformat()
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update({"access_token": new_access, "token_expiry": new_expiry})
        .eq("id", row["id"])
        .execute()
    )
    return new_access


async def list_workspaces(*, org_id: str, user_id: str) -> list[dict[str, Any]]:
    token = await _get_access_token(org_id=org_id, user_id=user_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_API}/workspaces",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 50},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"asana_workspaces_failed: {resp.status_code}")
    return (resp.json() or {}).get("data") or []


async def list_projects(
    *, org_id: str, user_id: str, workspace_gid: str
) -> list[dict[str, Any]]:
    token = await _get_access_token(org_id=org_id, user_id=user_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_API}/projects",
            headers={"Authorization": f"Bearer {token}"},
            params={"workspace": workspace_gid, "limit": 100},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"asana_projects_failed: {resp.status_code}")
    return (resp.json() or {}).get("data") or []


async def create_task(
    *,
    org_id: str,
    user_id: str,
    name: str,
    notes: str | None = None,
    assignee_email: str | None = None,
    due_date: str | None = None,  # YYYY-MM-DD
) -> dict[str, Any]:
    """POST /tasks. Returns {task_id, url}.

    Resolves project_gid + workspace_gid from `integrations.resources` —
    the user picked them at connect time. Without a project, Asana requires
    a workspace and an `assignee` — if both are missing we error out.
    """
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("asana_not_connected")
    token = await _get_access_token(org_id=org_id, user_id=user_id)
    resources = row.get("resources") or {}
    workspace = resources.get("workspace_gid")
    project = resources.get("default_project_gid")

    if not workspace and not project:
        raise PermissionError("asana_no_workspace_selected")

    payload: dict[str, Any] = {"name": name}
    if notes:
        payload["notes"] = notes
    if due_date:
        payload["due_on"] = due_date
    if project:
        payload["projects"] = [project]
    elif workspace:
        payload["workspace"] = workspace
    if assignee_email:
        # Asana accepts "me" or a user gid or an email — but only Premium
        # accounts accept email. We try; on 400 we drop assignee.
        payload["assignee"] = assignee_email

    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            f"{_API}/tasks",
            json={"data": payload},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    if resp.status_code == 400 and assignee_email:
        # Retry without assignee — email-as-assignee fails on free Asana.
        payload.pop("assignee", None)
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.post(
                f"{_API}/tasks",
                json={"data": payload},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

    if resp.status_code >= 400:
        raise RuntimeError(f"asana_create_failed: {resp.status_code} {resp.text[:200]}")

    data = (resp.json() or {}).get("data") or {}
    task_gid = data.get("gid")
    permalink = data.get("permalink_url") or f"https://app.asana.com/0/0/{task_gid}"
    return {"task_id": task_gid, "url": permalink}


async def get_task_status(*, org_id: str, user_id: str, task_gid: str) -> dict[str, Any]:
    """GET /tasks/{gid}. Returns the raw task — completed bool, due_on, etc."""
    token = await _get_access_token(org_id=org_id, user_id=user_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_API}/tasks/{task_gid}",
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code == 404:
        return {"status": "missing"}
    if resp.status_code >= 400:
        raise RuntimeError(f"asana_get_failed: {resp.status_code}")
    return (resp.json() or {}).get("data") or {}
