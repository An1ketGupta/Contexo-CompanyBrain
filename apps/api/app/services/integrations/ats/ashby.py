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

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


def _api() -> str:
    """Base URL for the Ashby Public API.

    Resolution order:
      1. USE_MOCK_ATS=true → MOCK_ATS_URL + "/ashby"
      2. ASHBY_API_URL set → that value
      3. default            → real api.ashbyhq.com
    """
    s = get_settings()
    if s.use_mock_ats:
        return f"{s.mock_ats_url.rstrip('/')}/ashby"
    return s.ashby_api_url.rstrip("/")


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
    location_ids = (metadata or {}).get("locationIds")
    if location_ids:
        body["locationIds"] = location_ids
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
            f"{_api()}/jobOpening.create",
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


async def fetch_candidates(*, org_id: str, job_id: str) -> list[dict[str, Any]]:
    """POST /application.list filtered by jobId → normalised candidate dicts.

    Ashby's application list embeds both the candidate object and the
    currentInterviewStage, so one paginated call covers the sync. The job
    opening id is what we stored as `job_id` at publish time.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("ashby_not_connected")
    api_key, _ = creds

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{_api()}/application.list",
            headers={
                "Authorization": _auth_header(api_key),
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"jobId": job_id, "limit": 100},
        )
    if resp.status_code == 401:
        raise PermissionError("ashby_unauthorized")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"ashby_candidates_failed: {resp.status_code} {resp.text[:200]}"
        )

    envelope = resp.json() or {}
    if not envelope.get("success"):
        raise RuntimeError(f"ashby_candidates_failed: {envelope.get('errors')}")

    results = envelope.get("results")
    if isinstance(results, dict):
        applications = results.get("data") or []
    elif isinstance(results, list):
        applications = results
    else:
        applications = []

    out: list[dict[str, Any]] = []
    for app in applications:
        cand = app.get("candidate") or {}
        cand_id = cand.get("id") or app.get("candidateId")
        if not cand_id:
            continue
        emails = cand.get("emailAddresses") or []
        phones = cand.get("phoneNumbers") or []
        stage_obj = app.get("currentInterviewStage") or {}
        stage = stage_obj.get("title") or stage_obj.get("name")

        out.append(
            {
                "ats_platform": "ashby",
                "external_id": str(cand_id),
                "full_name": cand.get("name") or None,
                "email": (emails[0] or {}).get("value") if emails else None,
                "phone": (phones[0] or {}).get("value") if phones else None,
                "current_company": cand.get("company") or None,
                "current_title": cand.get("position") or cand.get("title") or None,
                "stage": stage,
                "resume_url": cand.get("resumeFileHandle") or None,
                "candidate_url": f"https://app.ashbyhq.com/candidates/{cand_id}",
                "applied_at": app.get("createdAt"),
            }
        )
    return out


async def test_connection(*, api_key: str) -> bool:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(
            f"{_api()}/user.list",
            headers={
                "Authorization": _auth_header(api_key),
                "Content-Type": "application/json",
            },
            json={},
        )
    return resp.status_code == 200 and (resp.json() or {}).get("success", False)


# ── Taxonomy fetchers ────────────────────────────────────────────────────────


async def _post_list(api_key: str, endpoint: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.post(
            f"{_api()}/{endpoint}",
            headers={
                "Authorization": _auth_header(api_key),
                "Content-Type": "application/json",
            },
            json={},
        )
    if resp.status_code != 200:
        return []
    envelope = resp.json() or {}
    if not envelope.get("success"):
        return []
    results = envelope.get("results")
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        # Some Ashby endpoints wrap the list in {data:[...], moreDataAvailable}.
        return results.get("data") or []
    return []


async def list_locations(*, api_key: str) -> list[dict[str, Any]]:
    """POST /location.list → [{id, name}]."""
    raw = await _post_list(api_key, "location.list")
    return [
        {"id": item.get("id"), "name": item.get("name") or ""}
        for item in raw
        if item.get("id")
    ]


async def list_departments(*, api_key: str) -> list[dict[str, Any]]:
    """POST /department.list → [{id, name}]."""
    raw = await _post_list(api_key, "department.list")
    return [
        {"id": item.get("id"), "name": item.get("name") or ""}
        for item in raw
        if item.get("id")
    ]


async def list_teams(*, api_key: str) -> list[dict[str, Any]]:
    """POST /team.list → [{id, name}]."""
    raw = await _post_list(api_key, "team.list")
    return [
        {"id": item.get("id"), "name": item.get("name") or ""}
        for item in raw
        if item.get("id")
    ]


async def list_job_templates(*, api_key: str) -> list[dict[str, Any]]:
    """POST /jobTemplate.list → [{id, name}]. Required for jobOpening.create."""
    raw = await _post_list(api_key, "jobTemplate.list")
    return [
        {"id": item.get("id"), "name": item.get("name") or ""}
        for item in raw
        if item.get("id")
    ]
