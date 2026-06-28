"""Lever Postings API adapter.

Auth: Basic auth, API key as username, empty password (same as Greenhouse).
The token must be a *production* API key — sandbox keys hit a different host.

We use:
  POST /v1/postings           — create a posting (job)
  GET  /v1/users              — list users for the "Posting owner" picker

Lever's posting model has built-in distribution states (`internal`,
`published`, `closed`, `draft`, `pending`, `rejected`). New postings default
to `internal`; explicit publishing happens through the recruiter UI, mirroring
the Greenhouse pattern.
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
    """Base URL for the Lever Postings API.

    Resolution order:
      1. USE_MOCK_ATS=true → MOCK_ATS_URL + "/lever/v1"
      2. LEVER_API_URL set → that value
      3. default            → real api.lever.co
    """
    s = get_settings()
    if s.use_mock_ats:
        return f"{s.mock_ats_url.rstrip('/')}/lever/v1"
    return s.lever_api_url.rstrip("/")


async def _get_credentials(org_id: str) -> tuple[str, str | None] | None:
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("access_token, metadata")
        .eq("org_id", org_id)
        .eq("provider", "lever")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return (
        row.data["access_token"],
        (row.data.get("metadata") or {}).get("posting_owner_user_id"),
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
    """POST /v1/postings. Returns {job_id, url, raw}.

    Lever expects `content` as { description, descriptionHtml, lists,
    closingHtml }. We pass markdown as plain text in `description`; the
    recruiter can enrich the description inside Lever before publishing.

    `team` (= department) and `location` are simple strings; Lever resolves
    them to internal IDs on its side.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("lever_not_connected")
    api_key, owner_user_id = creds

    body: dict[str, Any] = {
        "text": title[:255],
        "state": "draft",
        "content": {
            "description": content,
            "descriptionHtml": "",
            "lists": [],
            "closing": "",
            "closingHtml": "",
        },
    }
    if owner_user_id:
        body["user"] = owner_user_id
    if location:
        body["categories"] = body.get("categories", {})
        body["categories"]["location"] = location
    if department:
        body.setdefault("categories", {})["team"] = department

    headers = {
        "Authorization": _auth_header(api_key),
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{_api()}/postings",
            json=body,
            headers=headers,
            # `perform_as` query param required when API key is account-wide
            # without an owner specified; harmless when owner_user_id is set.
            params={"perform_as": owner_user_id} if owner_user_id else None,
        )

    if resp.status_code == 401:
        raise PermissionError("lever_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("lever_forbidden")
    if resp.status_code >= 400:
        raise RuntimeError(f"lever_create_failed: {resp.status_code} {resp.text[:200]}")

    envelope = resp.json() or {}
    data = envelope.get("data") or envelope
    posting_id = data.get("id")
    url = data.get("urls", {}).get("show") or f"https://hire.lever.co/postings/{posting_id}"
    return {
        "job_id": str(posting_id),
        "url": url,
        "raw": data,
    }


async def fetch_candidates(*, org_id: str, job_id: str) -> list[dict[str, Any]]:
    """GET /v1/opportunities?posting_id=… → normalised CandidateRecord dicts.

    Lever calls candidates "opportunities" — every record is one candidate's
    relationship with the company, scoped to one posting. `expand=stage`
    inlines the stage object so we don't N+1 against /v1/stages.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("lever_not_connected")
    api_key, _ = creds

    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.get(
            f"{_api()}/opportunities",
            params={
                "posting_id": job_id,
                "limit": 100,
                "expand": "stage",
            },
            headers={"Authorization": _auth_header(api_key)},
        )
    if resp.status_code == 401:
        raise PermissionError("lever_unauthorized")
    if resp.status_code >= 400:
        raise RuntimeError(
            f"lever_candidates_failed: {resp.status_code} {resp.text[:200]}"
        )

    envelope = resp.json() or {}
    opportunities = envelope.get("data") or []
    for op in opportunities:
        opp_id = op.get("id")
        if not opp_id:
            continue
        emails = op.get("emails") or []
        phones = op.get("phones") or []
        organizations = op.get("organizations") or []
        stage_obj = op.get("stage")
        # `expand=stage` returns either a string id or an inlined object.
        if isinstance(stage_obj, dict):
            stage = stage_obj.get("text") or stage_obj.get("id")
        else:
            stage = stage_obj if isinstance(stage_obj, str) else None

        created_at = op.get("createdAt")
        applied_at: str | None = None
        if isinstance(created_at, int):
            # Lever returns ms timestamps; convert to ISO so the upstream
            # Notion writer doesn't need to know that.
            from datetime import datetime as _dt

            applied_at = _dt.utcfromtimestamp(created_at / 1000).isoformat() + "Z"
        elif isinstance(created_at, str):
            applied_at = created_at

        out.append(
            {
                "ats_platform": "lever",
                "external_id": str(opp_id),
                "full_name": op.get("name") or None,
                "email": emails[0] if emails else None,
                "phone": (phones[0] or {}).get("value") if phones else None,
                "current_company": organizations[0] if organizations else None,
                "current_title": op.get("headline") or None,
                "stage": stage,
                "resume_url": None,  # Lever resume requires /resumes call — skip for V1.
                "candidate_url": (op.get("urls") or {}).get("show")
                or f"https://hire.lever.co/candidates/{opp_id}",
                "applied_at": applied_at,
            }
        )
    return out


async def list_users(*, org_id: str) -> list[dict[str, Any]]:
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("lever_not_connected")
    api_key, _ = creds
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_api()}/users",
            headers={"Authorization": _auth_header(api_key)},
            params={"limit": 100},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"lever_users_failed: {resp.status_code}")
    return (resp.json() or {}).get("data") or []


async def test_connection(*, api_key: str) -> bool:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{_api()}/users",
            params={"limit": 1},
            headers={"Authorization": _auth_header(api_key)},
        )
    return resp.status_code == 200


# ── Taxonomy fetchers ────────────────────────────────────────────────────────
# Lever doesn't expose dedicated location/department endpoints — both are
# free-text strings on postings. The "taxonomy" we cache is the distinct set
# of categories.location and categories.team values seen across recent
# postings, so the resolver can still suggest deterministic matches.


async def list_locations(*, api_key: str) -> list[dict[str, Any]]:
    """Distinct categories.location from the last 200 postings."""
    return await _distinct_category(api_key, key="location")


async def list_teams(*, api_key: str) -> list[dict[str, Any]]:
    """Distinct categories.team from the last 200 postings — Lever calls
    departments "teams"."""
    return await _distinct_category(api_key, key="team")


async def _distinct_category(api_key: str, *, key: str) -> list[dict[str, Any]]:
    values: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        resp = await client.get(
            f"{_api()}/postings",
            params={"limit": 100, "include": "categories"},
            headers={"Authorization": _auth_header(api_key)},
        )
        if resp.status_code != 200:
            return []
        for posting in (resp.json() or {}).get("data") or []:
            val = ((posting.get("categories") or {}).get(key) or "").strip()
            if val:
                values[val] = values.get(val, 0) + 1
    # Sort by frequency so the most-used values surface first in pickers.
    return [
        {"id": v, "name": v, "count": c}
        for v, c in sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
