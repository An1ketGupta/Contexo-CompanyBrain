"""Jira Cloud adapter (per-user OAuth 3LO, REST API v3).

Surface mirrors the Linear adapter so the action_tracker can route
interchangeably:
    list_projects(org_id, user_id)
    create_issue(org_id, user_id, title, description, assignee_email, due_date)
    get_issue_status(org_id, user_id, issue_id_or_key)

Storage: `integrations` row, provider='jira', scope_user_id=user_id.
  metadata: {cloud_id, site_url, account_email}
  resources: {project_key, project_id, default_issue_type}

OAuth 3LO flow:
    1. /authorize → consent → callback with code
    2. exchange code at https://auth.atlassian.com/oauth/token (refresh-token
       capable when `offline_access` scope was requested)
    3. GET https://api.atlassian.com/oauth/token/accessible-resources to
       resolve the cloud_id for the user's selected site

All Jira REST calls go through https://api.atlassian.com/ex/jira/{cloudId}/...
which is the OAuth-aware proxy; per-site URLs are not used.

Descriptions must be ADF (Atlassian Document Format) — a tagged JSON tree.
We send the minimal valid ADF for a plain paragraph so we don't have to
ship a Markdown→ADF converter.
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

PROVIDER = "jira"
_AUTH_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"


# ── Row + token helpers ────────────────────────────────────────────────────


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


async def _refresh_if_expiring(row: dict[str, Any]) -> dict[str, Any]:
    """Atlassian access tokens expire (typically 1h). Refresh when within 60s
    of expiry. Updates the integrations row in place."""
    expiry_raw = row.get("token_expiry")
    if not expiry_raw or not row.get("refresh_token"):
        return row
    try:
        expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return row
    if expiry > datetime.now(UTC) + timedelta(seconds=60):
        return row

    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            _AUTH_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": settings.jira_client_id,
                "client_secret": settings.jira_client_secret,
                "refresh_token": row["refresh_token"],
            },
        )
    if resp.status_code != 200:
        raise PermissionError(f"jira_refresh_failed:{resp.status_code}")
    payload = resp.json()
    new_expiry = (
        datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in") or 3500))
    ).isoformat()
    updates = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or row["refresh_token"],
        "token_expiry": new_expiry,
    }
    svc = get_service_client()
    await asyncio.to_thread(
        lambda: svc.table("integrations")
        .update(updates)
        .eq("id", row["id"])
        .execute()
    )
    return {**row, **updates}


async def _get_authed(*, org_id: str, user_id: str) -> tuple[str, str]:
    """Returns (access_token, cloud_id)."""
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("jira_not_connected")
    row = await _refresh_if_expiring(row)
    cloud_id = (row.get("metadata") or {}).get("cloud_id")
    if not cloud_id:
        raise PermissionError("jira_no_cloud_id")
    return row["access_token"], cloud_id


def _api_base(cloud_id: str) -> str:
    return f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3"


async def _request(
    method: str,
    *,
    org_id: str,
    user_id: str,
    path: str,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    token, cloud_id = await _get_authed(org_id=org_id, user_id=user_id)
    url = f"{_api_base(cloud_id)}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=json_body,
            params=params,
        )
    if resp.status_code == 401:
        raise PermissionError("jira_unauthorized")
    if resp.status_code >= 400:
        # Jira returns useful error messages under errorMessages / errors
        try:
            err = resp.json()
        except Exception:
            err = {"raw": resp.text[:200]}
        raise RuntimeError(f"jira_http_{resp.status_code}: {err}")
    if not resp.content:
        return {}
    return resp.json()


# ── OAuth helpers used by the router ───────────────────────────────────────


async def list_accessible_resources(*, access_token: str) -> list[dict[str, Any]]:
    """Calls /accessible-resources to enumerate sites the user granted us.

    The user picks one and we store its `id` as `metadata.cloud_id`.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            _RESOURCES_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"jira_resources_failed:{resp.status_code}")
    return resp.json() or []


# ── Public API ─────────────────────────────────────────────────────────────


async def list_projects(*, org_id: str, user_id: str) -> list[dict[str, Any]]:
    """Returns up to 50 projects on the connected Jira site."""
    data = await _request(
        "GET",
        org_id=org_id,
        user_id=user_id,
        path="/project/search",
        params={"maxResults": "50"},
    )
    return [
        {"id": p.get("id"), "key": p.get("key"), "name": p.get("name")}
        for p in (data.get("values") or [])
    ]


async def _resolve_account_id_by_email(
    *, org_id: str, user_id: str, email: str
) -> str | None:
    """Jira's /user/search supports `query=email@host` for visible users."""
    try:
        data = await _request(
            "GET",
            org_id=org_id,
            user_id=user_id,
            path="/user/search",
            params={"query": email},
        )
    except Exception as exc:
        log.warning("jira.user_search.failed email=%s err=%s", email, exc)
        return None
    if isinstance(data, list) and data:
        return data[0].get("accountId")
    # `/user/search` returns a raw list in v3 — fall through if shape differs.
    if isinstance(data, dict):
        values = data.get("values") or []
        if values:
            return values[0].get("accountId")
    return None


def _adf_from_text(text: str) -> dict[str, Any]:
    """Minimal valid ADF document carrying a single paragraph.

    Newlines split into multiple paragraphs to preserve structure.
    """
    paragraphs = [p for p in (text or "").split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": p[:30_000]}],
            }
            for p in paragraphs
        ],
    }


async def create_issue(
    *,
    org_id: str,
    user_id: str,
    title: str,
    description: str | None,
    assignee_email: str | None,
    due_date: str | None,  # YYYY-MM-DD
) -> dict[str, Any]:
    """Create an issue in the connected project. Returns {task_id, key, url}."""
    row = await _get_row(org_id=org_id, user_id=user_id)
    if not row:
        raise PermissionError("jira_not_connected")
    resources = row.get("resources") or {}
    project_key = resources.get("project_key")
    if not project_key:
        raise PermissionError("jira_no_project_selected")
    issue_type = resources.get("default_issue_type") or "Task"

    fields: dict[str, Any] = {
        "project": {"key": project_key},
        "summary": title[:255],
        "issuetype": {"name": issue_type},
    }
    if description:
        fields["description"] = _adf_from_text(description)
    if due_date:
        fields["duedate"] = due_date
    if assignee_email:
        account_id = await _resolve_account_id_by_email(
            org_id=org_id, user_id=user_id, email=assignee_email
        )
        if account_id:
            fields["assignee"] = {"accountId": account_id}

    created = await _request(
        "POST",
        org_id=org_id,
        user_id=user_id,
        path="/issue",
        json_body={"fields": fields},
    )
    key = created.get("key")
    site_url = (row.get("metadata") or {}).get("site_url") or ""
    url = f"{site_url.rstrip('/')}/browse/{key}" if (site_url and key) else None
    return {
        "task_id": created.get("id") or key,
        "identifier": key,
        "url": url,
    }


async def get_issue_status(
    *, org_id: str, user_id: str, issue_id: str
) -> dict[str, Any]:
    """Returns {state, completed_at, identifier, state_category} or {status: 'missing'}."""
    try:
        data = await _request(
            "GET",
            org_id=org_id,
            user_id=user_id,
            path=f"/issue/{issue_id}",
            params={"fields": "status,resolutiondate,summary"},
        )
    except RuntimeError as exc:
        if "jira_http_404" in str(exc):
            return {"status": "missing"}
        raise
    fields = data.get("fields") or {}
    status_obj = fields.get("status") or {}
    category = (status_obj.get("statusCategory") or {}).get("key")
    return {
        "id": data.get("id"),
        "identifier": data.get("key"),
        "completed_at": fields.get("resolutiondate"),
        "state": status_obj.get("name"),
        "state_category": category,  # 'done' | 'indeterminate' | 'new'
    }
