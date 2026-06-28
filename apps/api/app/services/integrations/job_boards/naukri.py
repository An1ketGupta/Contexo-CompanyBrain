"""Naukri HotVacancy API adapter.

Wire format
-----------
Naukri (Info Edge) does not publish an open developer portal. The shapes
encoded here mirror what the enterprise HotVacancy SDK documents at the
point of contract signing — they are the canonical wire shapes for the
mock server in `tools/mock_ats_server.py` and the same shapes the
adapter sends against `naukri_api_url`. If the real API rejects a field
on first contact, fix the shape here and in the mock together so the
smoke test continues to exercise the production path.

Auth
----
Naukri uses an `Auth-Key` header (NOT HTTP Basic) carrying the recruiter's
account API key. There's also an `Account-Id` header on most endpoints
that scopes the request to a corporate account when one user has access
to multiple. Both are stored in `integrations.access_token` and
`integrations.metadata.account_id` respectively.

Endpoints used
--------------
  POST /jobposting                     create / publish a HotVacancy
  GET  /ping                           connectivity check
  GET  /taxonomy/functionalAreas       master list of Functional Areas
  GET  /taxonomy/roleCategories        master list of Role Categories
  GET  /taxonomy/industries            master list of Industry Types

Endpoints NOT used (yet)
------------------------
  /resdex/*  — paid candidate-search API. Phase 2 work. We currently
               generate deep links to Naukri's web UI for sourcing
               instead of consuming this API directly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.database import get_service_client

log = logging.getLogger(__name__)


# ── Resolver helpers ────────────────────────────────────────────────────────


def _api() -> str:
    """Base URL for the Naukri HotVacancy API.

    Resolution order:
      1. USE_MOCK_ATS=true       → MOCK_ATS_URL + "/naukri/v1"
      2. NAUKRI_API_URL set      → that value
      3. default                  → real api.naukri.com/v1
    """
    s = get_settings()
    if s.use_mock_ats:
        return f"{s.mock_ats_url.rstrip('/')}/naukri/v1"
    return s.naukri_api_url.rstrip("/")


def _headers(api_key: str, account_id: str | None = None) -> dict[str, str]:
    """Common headers for every Naukri call.

    Auth-Key is Naukri's documented header name (case-sensitive). Account-Id
    scopes the call to a particular corporate account when set; omit on calls
    that don't need it (e.g. taxonomy lookups) to keep the request lean.
    """
    h = {
        "Auth-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        # Naukri's gateway uses this to identify the integration partner.
        # Harmless to send unconditionally.
        "X-Application-Source": "nirnayaiq",
    }
    if account_id:
        h["Account-Id"] = account_id
    return h


async def _get_credentials(org_id: str) -> tuple[str, dict[str, Any]] | None:
    """Returns (api_key, metadata) or None when the org hasn't connected Naukri."""
    svc = get_service_client()
    row = await asyncio.to_thread(
        lambda: svc.table("integrations")
        .select("access_token, metadata")
        .eq("org_id", org_id)
        .eq("provider", "naukri")
        .is_("scope_user_id", None)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        return None
    return row.data["access_token"], row.data.get("metadata") or {}


# ── Error mapping ───────────────────────────────────────────────────────────


def _raise_for_status(resp: httpx.Response, *, op: str) -> None:
    """Translate Naukri status codes into the same exception classes the
    rest of the recruiting flow already handles.

    PermissionError → caller surfaces as a 400 "not connected / unauthorized"
    RuntimeError    → caller surfaces as a 502 "create failed"
    """
    if resp.status_code == 401:
        raise PermissionError("naukri_unauthorized")
    if resp.status_code == 403:
        raise PermissionError("naukri_forbidden")
    if resp.status_code == 429:
        # Naukri imposes per-account QPS limits. The publish flow has no
        # retry budget here (we publish once, surface a clear error). The
        # recruiter retries from the UI when the rate window clears.
        raise RuntimeError("naukri_rate_limited")
    if resp.status_code >= 400:
        # Truncate body to 200 chars so the error message stays HTTP-header
        # safe when the audit log puts it through email/Slack downstream.
        body = (resp.text or "")[:200]
        raise RuntimeError(f"naukri_{op}_failed: {resp.status_code} {body}")


# ── Connection probe ────────────────────────────────────────────────────────


async def test_connection(*, api_key: str) -> bool:
    """Connectivity + auth check. Used by the connect flow before persisting
    the API key. Hits GET /ping — cheap, no taxonomy round-trip."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        try:
            resp = await client.get(f"{_api()}/ping", headers=_headers(api_key))
        except httpx.HTTPError as exc:
            log.warning("naukri.test_connection.error err=%s", exc)
            return False
    return resp.status_code == 200


# ── Publish ─────────────────────────────────────────────────────────────────


def _experience_band(metadata: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Pull (min_years, max_years) from publish metadata, validating bounds.

    Naukri rejects payloads where max < min — we fail fast at the boundary
    rather than letting their API surface a confusing 400."""
    if not metadata:
        return None, None
    lo = metadata.get("experience_min_years")
    hi = metadata.get("experience_max_years")
    try:
        lo_i = int(lo) if lo is not None else None
        hi_i = int(hi) if hi is not None else None
    except (TypeError, ValueError):
        return None, None
    if lo_i is not None and hi_i is not None and hi_i < lo_i:
        # Swap rather than reject — recruiter typo'd, intent is clear.
        lo_i, hi_i = hi_i, lo_i
    return lo_i, hi_i


async def publish_job(
    *,
    org_id: str,
    title: str,
    content: str,
    location: str | None = None,
    department: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /jobposting. Returns {job_id, url, raw}.

    `metadata` carries the recruiter's Naukri taxonomy choices made in the
    publish form: functional_area_id, role_category_id, industry_type_id,
    experience_min_years, experience_max_years, key_skills (list[str]).

    These are required by Naukri; we surface a PermissionError-like 400 (via
    the router's error mapping) when they're missing rather than letting the
    real API reject a half-formed payload.
    """
    creds = await _get_credentials(org_id)
    if not creds:
        raise PermissionError("naukri_not_connected")
    api_key, account_meta = creds
    account_id = account_meta.get("account_id")

    meta = metadata or {}
    min_years, max_years = _experience_band(meta)

    # Naukri's HotVacancy create body. Keys mirror the documented contract.
    # `jobDescription` accepts markdown — Naukri's web UI renders it; some
    # older accounts strip markdown to plain text on display. Either way our
    # JD content is preserved server-side.
    body: dict[str, Any] = {
        # Truncate at 100 chars — Naukri's documented title limit is shorter
        # than the ATS providers' 255.
        "jobTitle": title[:100],
        "jobDescription": content,
        # Empty string is rejected; Naukri requires *some* location string.
        "jobLocation": (location or "India")[:200],
    }
    if department:
        body["department"] = department[:200]
    if meta.get("functional_area_id"):
        body["functionalAreaId"] = meta["functional_area_id"]
    if meta.get("role_category_id"):
        body["roleCategoryId"] = meta["role_category_id"]
    if meta.get("industry_type_id"):
        body["industryTypeId"] = meta["industry_type_id"]
    if min_years is not None:
        body["minExperience"] = min_years
    if max_years is not None:
        body["maxExperience"] = max_years
    skills = meta.get("key_skills") or []
    if isinstance(skills, list) and skills:
        # Naukri caps key skills at 12 in the UI. Slice defensively even
        # though the Pydantic model already enforces this — defense in depth
        # against direct service-layer callers (Inngest workers, smoke tests).
        body["keySkills"] = [str(s)[:50] for s in skills[:12]]
    # Optional comp disclosure — Naukri's "Hide Salary" boolean. We default
    # to true when no comp passed because most recruiters in India treat the
    # range as confidential.
    if meta.get("disclosed_compensation"):
        body["salary"] = meta["disclosed_compensation"][:100]
        body["hideSalary"] = False
    else:
        body["hideSalary"] = True

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{_api()}/jobposting",
            json=body,
            headers=_headers(api_key, account_id),
        )

    _raise_for_status(resp, op="create")

    data = resp.json() if resp.content else {}
    job_id = data.get("jobId") or data.get("id")
    if not job_id:
        # Defensive — every documented success response carries an id. If
        # Naukri ever returns 200 with no id we want to know about it
        # rather than silently storing None.
        raise RuntimeError(f"naukri_create_no_id: {data!r}")
    # `postingUrl` is the canonical recruiter-facing URL inside Naukri's
    # employer dashboard. Falls back to the candidate-facing job page if the
    # API didn't send one.
    url = (
        data.get("postingUrl")
        or data.get("url")
        or f"https://www.naukri.com/job-listings-{job_id}"
    )
    return {
        "job_id": str(job_id),
        "url": url,
        "raw": data,
    }


# ── Taxonomy fetchers (used by mapping_resolver.refresh_cache) ──────────────


async def _paginated_get(
    api_key: str,
    path: str,
    *,
    per_page: int = 200,
    max_pages: int = 5,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Naukri paginates with ?page=N&pageSize=M. Cap at max_pages so a
    pathological account (5000+ industries) doesn't blow the timeout."""
    out: list[dict[str, Any]] = []
    headers = _headers(api_key, account_id)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        for page in range(1, max_pages + 1):
            resp = await client.get(
                f"{_api()}{path}",
                params={"page": page, "pageSize": per_page},
                headers=headers,
            )
            if resp.status_code != 200:
                break
            payload = resp.json() if resp.content else {}
            # Naukri wraps lists in {data: [...]} or {results: [...]}. Mock
            # uses {data: [...]} to match what the real gateway returns.
            batch = (
                payload.get("data")
                or payload.get("results")
                or (payload if isinstance(payload, list) else [])
            )
            if not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
    return out


def _coerce(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise taxonomy items to {id, name}. Naukri's payloads sometimes
    use `id`/`name`, sometimes `code`/`label`. We accept both."""
    out: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("id") or item.get("code")
        name = item.get("name") or item.get("label")
        if item_id is None or not name:
            continue
        out.append({"id": str(item_id), "name": str(name)})
    return out


async def list_functional_areas(*, api_key: str) -> list[dict[str, Any]]:
    """GET /taxonomy/functionalAreas — Naukri's top-level job family taxonomy.

    Examples: "IT-Software / Software Services", "Sales / Business
    Development", "HR / Recruitment / Admin"."""
    raw = await _paginated_get(api_key, "/taxonomy/functionalAreas")
    return _coerce(raw)


async def list_role_categories(*, api_key: str) -> list[dict[str, Any]]:
    """GET /taxonomy/roleCategories. One level below functional area.

    Examples: "Programming & Design", "Quality Assurance / Testing",
    "Field Sales", "Recruitment / Talent Acquisition"."""
    raw = await _paginated_get(api_key, "/taxonomy/roleCategories")
    return _coerce(raw)


async def list_industries(*, api_key: str) -> list[dict[str, Any]]:
    """GET /taxonomy/industries — the candidate's current industry filter
    (recruiter typically wants candidates from same / adjacent industries).

    Examples: "IT-Software / Software Services", "BPO / ITES", "Banking /
    Financial Services / Broking"."""
    raw = await _paginated_get(api_key, "/taxonomy/industries")
    return _coerce(raw)
