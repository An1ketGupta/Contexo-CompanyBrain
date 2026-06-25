"""Greenhouse Harvest API adapter.

Auth: Basic auth, API key as username, empty password. The header
`On-Behalf-Of` carries the Greenhouse user_id who owns the job — that's the
hiring lead or recruiter on the org's Greenhouse side, NOT our user_id.
Stored in `integrations.metadata.on_behalf_of_user_id`.

We use two endpoints:
  POST /v1/jobs           — create a job (returns id + html link)
  GET  /v1/users          — connectivity check / list users for the dropdown

We do NOT create job_posts here. The Recruiting service's MVP flow is:
"create the job + capture the URL"; the recruiter publishes the post manually
inside Greenhouse where the templating + board logic lives. Auto-creating
posts requires more config (offices, departments, custom fields) than is
sane to mirror in our UI.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from app.database import get_service_client

log = logging.getLogger(__name__)

_API = "https://harvest.greenhouse.io/v1"


async def _get_credentials(org_id: str) -> tuple[str, str | None] | None:
    """Returns (api_key, on_behalf_of_user_id?) or None when not connected."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("access_token, metadata")
        .eq("org_id", org_id)
        .eq("provider", "greenhouse")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return (
        row.data["access_token"],
        (row.data.get("metadata") or {}).get("on_behalf_of_user_id"),
    )


def _auth_header(api_key: str) -> str:
    token = base64.b64encode(f"{api_key}:".encode()).decode("ascii")
    return f"Basic {token}"


async def publish_job(
    *,
    org_id: str,
    title: str,
    content: str,
    location: str | None = None,
    department: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /v1/jobs. Returns {job_id, url, raw}.

    Greenhouse's create-job payload is small; most rich data (custom_fields,
    offices) needs IDs we don't have. We send the minimum viable shape and
    let the recruiter complete configuration inside Greenhouse.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("greenhouse_not_connected")
    api_key, on_behalf = creds

    body: dict[str, Any] = {
        "name": title[:255],
        "notes": content,  # plain-text body; preserves line breaks for JD review
        "requisition_id": (metadata or {}).get("requisition_id"),
    }
    if location:
        body["office_ids"] = (metadata or {}).get("office_ids") or []
        # Greenhouse takes office IDs, not strings. We keep `location` text
        # available so the recruiter can paste it into the job's location
        # field manually if no office mapping exists yet.
    if department:
        body["department_id"] = (metadata or {}).get("department_id")

    headers = {
        "Authorization": _auth_header(api_key),
        "Content-Type": "application/json",
    }
    if on_behalf:
        headers["On-Behalf-Of"] = on_behalf

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(f"{_API}/jobs", json=body, headers=headers)

    if resp.status_code == 401:
        raise PermissionError("greenhouse_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("greenhouse_forbidden")
    if resp.status_code >= 400:
        raise RuntimeError(f"greenhouse_create_failed: {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    job_id = data.get("id")
    # Greenhouse doesn't return a UI URL on create — construct it from the
    # account subdomain stored at connect time.
    metadata_row = await _get_account_metadata(org_id)
    subdomain = (metadata_row or {}).get("board_subdomain") or "app"
    url = f"https://{subdomain}.greenhouse.io/sdash/{job_id}"
    return {
        "job_id": str(job_id),
        "url": url,
        "raw": data,
    }


async def _get_account_metadata(org_id: str) -> dict[str, Any] | None:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("metadata")
        .eq("org_id", org_id)
        .eq("provider", "greenhouse")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    return (row.data or {}).get("metadata") if (row and row.data) else None


async def list_users(*, org_id: str) -> list[dict[str, Any]]:
    """GET /v1/users — used by the connect flow's "On-behalf-of" picker."""
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("greenhouse_not_connected")
    api_key, _ = creds
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_API}/users",
            headers={"Authorization": _auth_header(api_key)},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"greenhouse_users_failed: {resp.status_code}")
    return resp.json() or []


async def test_connection(*, api_key: str) -> bool:
    """Sanity-check an API key by hitting /v1/users with limit=1."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_API}/users",
            params={"per_page": 1},
            headers={"Authorization": _auth_header(api_key)},
        )
    return resp.status_code == 200
