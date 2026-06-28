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

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


def _api() -> str:
    """Base URL for the Greenhouse Harvest API.

    Resolution order:
      1. USE_MOCK_ATS=true       → MOCK_ATS_URL + "/greenhouse/v1"
      2. GREENHOUSE_API_URL set  → that value
      3. default                  → real harvest.greenhouse.io
    """
    s = get_settings()
    if s.use_mock_ats:
        return f"{s.mock_ats_url.rstrip('/')}/greenhouse/v1"
    return s.greenhouse_api_url.rstrip("/")


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
    office_ids = (metadata or {}).get("office_ids")
    if office_ids:
        body["office_ids"] = office_ids
    if department:
        body["department_id"] = (metadata or {}).get("department_id")

    headers = {
        "Authorization": _auth_header(api_key),
        "Content-Type": "application/json",
    }
    if on_behalf:
        headers["On-Behalf-Of"] = on_behalf

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(f"{_api()}/jobs", json=body, headers=headers)

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


async def fetch_candidates(*, org_id: str, job_id: str) -> list[dict[str, Any]]:
    """GET /v1/candidates?job_id=… → normalised CandidateRecord dicts.

    Greenhouse's candidate object embeds applications (with current_stage)
    and the contact arrays, so one paginated request is enough — no N+1
    against /v1/applications. We cap at 500 candidates per job; anything
    beyond that is a pipeline that needs its own export, not a sync.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("greenhouse_not_connected")
    api_key, _ = creds

    raw = await _paginated_get(
        api_key, f"/candidates?job_id={job_id}", per_page=100, max_pages=5
    )
    out: list[dict[str, Any]] = []
    for c in raw:
        candidate_id = c.get("id")
        if candidate_id is None:
            continue
        first = (c.get("first_name") or "").strip()
        last = (c.get("last_name") or "").strip()
        full_name = (f"{first} {last}".strip()) or None

        emails = c.get("email_addresses") or []
        email = emails[0].get("value") if emails else None
        phones = c.get("phone_numbers") or []
        phone = phones[0].get("value") if phones else None

        # The applications array is filtered by Greenhouse to those on this
        # job. We surface the FIRST application's stage — typical case is
        # one application per (candidate, job) anyway.
        applications = c.get("applications") or []
        primary_app = applications[0] if applications else {}
        stage_obj = primary_app.get("current_stage") or {}
        stage = stage_obj.get("name")
        applied_at = primary_app.get("applied_at")

        attachments = c.get("attachments") or []
        resume_url = next(
            (
                a.get("url")
                for a in attachments
                if (a.get("type") or "").lower() == "resume"
            ),
            None,
        )

        # Greenhouse candidate URLs aren't returned in the payload; we
        # construct them from the account subdomain captured at connect time.
        meta = await _get_account_metadata(org_id)
        subdomain = (meta or {}).get("board_subdomain") or "app"
        candidate_url = f"https://{subdomain}.greenhouse.io/people/{candidate_id}"

        out.append(
            {
                "ats_platform": "greenhouse",
                "external_id": str(candidate_id),
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "current_company": c.get("company") or None,
                "current_title": c.get("title") or None,
                "stage": stage,
                "resume_url": resume_url,
                "candidate_url": candidate_url,
                "applied_at": applied_at,
            }
        )
    return out


async def list_users(*, org_id: str) -> list[dict[str, Any]]:
    """GET /v1/users — used by the connect flow's "On-behalf-of" picker."""
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("greenhouse_not_connected")
    api_key, _ = creds
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_api()}/users",
            headers={"Authorization": _auth_header(api_key)},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"greenhouse_users_failed: {resp.status_code}")
    return resp.json() or []


async def test_connection(*, api_key: str) -> bool:
    """Sanity-check an API key by hitting /v1/users with limit=1."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_api()}/users",
            params={"per_page": 1},
            headers={"Authorization": _auth_header(api_key)},
        )
    return resp.status_code == 200


# ── Taxonomy fetchers used by the mapping resolver ──────────────────────────


async def _paginated_get(
    api_key: str, path: str, *, per_page: int = 100, max_pages: int = 5
) -> list[dict[str, Any]]:
    """Greenhouse paginates with ?page=N&per_page=M. We cap at max_pages so a
    customer with 5000 offices doesn't blow the timeout — 500 is plenty for
    the mapping cache."""
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        for page in range(1, max_pages + 1):
            resp = await client.get(
                f"{_api()}{path}",
                params={"page": page, "per_page": per_page},
                headers={"Authorization": _auth_header(api_key)},
            )
            if resp.status_code != 200:
                break
            batch = resp.json() or []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
    return out


async def list_offices(*, api_key: str) -> list[dict[str, Any]]:
    """GET /v1/offices. Returns [{id, name, location}]."""
    raw = await _paginated_get(api_key, "/offices")
    return [
        {
            "id": str(item.get("id")),
            "name": item.get("name") or "",
            "location": (item.get("location") or {}).get("name"),
        }
        for item in raw
        if item.get("id") is not None
    ]


async def list_departments(*, api_key: str) -> list[dict[str, Any]]:
    """GET /v1/departments. Returns [{id, name, parent_id}]."""
    raw = await _paginated_get(api_key, "/departments")
    return [
        {
            "id": str(item.get("id")),
            "name": item.get("name") or "",
            "parent_id": str(item.get("parent_id")) if item.get("parent_id") else None,
        }
        for item in raw
        if item.get("id") is not None
    ]
