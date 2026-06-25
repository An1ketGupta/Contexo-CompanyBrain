"""Ashby Public API adapter.

Auth: Basic auth, API key as username, empty password.

Ashby separates "job openings" (org's intent to hire) from "jobs" (active
requisitions linked to a job board). We call POST /jobOpening.create — the
minimum viable shape — and capture the returned id; the recruiter can then
attach a job board posting inside Ashby.

Endpoint shape note: Ashby's "Public API" uses POST for everything, including
reads. There's no GET /v1/jobs equivalent; lists are POST /jobOpening.list.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from app.database import get_service_client

log = logging.getLogger(__name__)

_API = "https://api.ashbyhq.com"


async def _get_credentials(org_id: str) -> tuple[str, str | None] | None:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("access_token, metadata")
        .eq("org_id", org_id)
        .eq("provider", "ashby")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return (
        row.data["access_token"],
        (row.data.get("metadata") or {}).get("hiring_team_member_id"),
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
    """POST /jobOpening.create. Returns {job_id, url, raw}."""
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("ashby_not_connected")
    api_key, owner_id = creds

    body: dict[str, Any] = {
        "jobTemplateId": (metadata or {}).get("jobTemplateId"),
        "teamId": (metadata or {}).get("teamId"),
        "title": title[:255],
        "description": content,
    }
    if location:
        body["locationIds"] = (metadata or {}).get("locationIds") or []
    if department:
        body["departmentId"] = (metadata or {}).get("departmentId")
    if owner_id:
        body["hiringTeam"] = [
            {"userId": owner_id, "teamMemberType": "Recruiter"}
        ]

    # Drop None values — Ashby rejects nulls on optional fields with a 400.
    body = {k: v for k, v in body.items() if v is not None}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{_API}/jobOpening.create",
            json=body,
            headers={
                "Authorization": _auth_header(api_key),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    if resp.status_code == 401:
        raise PermissionError("ashby_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("ashby_forbidden")
    if resp.status_code >= 400:
        raise RuntimeError(f"ashby_create_failed: {resp.status_code} {resp.text[:200]}")

    envelope = resp.json() or {}
    if not envelope.get("success", False):
        # Ashby returns { success: false, errors: [...] } on logical failures
        # even with HTTP 200.
        raise RuntimeError(f"ashby_create_failed: {envelope.get('errors')}")

    data = envelope.get("results") or {}
    opening_id = data.get("id")
    # Ashby URL pattern: https://app.ashbyhq.com/admin/job-openings/<id>
    url = f"https://app.ashbyhq.com/admin/job-openings/{opening_id}"
    return {
        "job_id": str(opening_id),
        "url": url,
        "raw": data,
    }


async def test_connection(*, api_key: str) -> bool:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_API}/user.list",
            headers={
                "Authorization": _auth_header(api_key),
                "Content-Type": "application/json",
            },
            json={},
        )
    return resp.status_code == 200 and (resp.json() or {}).get("success", False)
