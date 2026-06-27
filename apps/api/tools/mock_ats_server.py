"""Mock ATS server — Greenhouse Harvest, Lever Postings, Ashby Public API.

Why this exists
---------------
Greenhouse / Lever / Ashby are enterprise sales-only products. There's no
self-serve developer tier we can use to exercise the publish loop end-to-end
in dev or CI. This server stands in for all three and returns responses
shaped exactly like the real APIs, so the rest of our integration code path
(mapping resolver → adapter → audit log → idempotency → email) is exercised
against realistic payloads.

It is NOT a fixture: state is preserved across requests within a process,
each "tenant" (API key) gets its own job ledger and taxonomy, and the wire
shapes are accurate enough that our adapters' `test_connection()`,
`list_*()`, and `publish_job()` paths all succeed without modification.

What's covered
--------------
Greenhouse (`/greenhouse/v1/`)
  GET  /users               Auth probe + on-behalf-of picker
  POST /jobs                Create job
  GET  /offices             Taxonomy
  GET  /departments         Taxonomy

Lever (`/lever/v1/`)
  GET  /users               Auth probe + posting owner picker
  GET  /postings            Returns recent postings for distinct-category derivation
  POST /postings            Create posting

Ashby (`/ashby/`)
  POST /user.list           Auth probe + hiring team picker
  POST /jobOpening.create   Create job opening
  POST /location.list       Taxonomy
  POST /department.list     Taxonomy
  POST /team.list           Taxonomy
  POST /jobTemplate.list    Taxonomy

Operational
  GET  /__health           Liveness
  POST /__reset            Wipe all tenant state (CI use)
  GET  /__inspect/{key}    Dump a tenant's jobs/taxonomy (debug)

Auth model
----------
Basic auth, API key as the username. Any non-empty username is accepted —
this is a *mock*, not a security boundary. Each distinct key gets its own
isolated tenant store, so two parallel CI runs with different keys won't
collide.

Error injection
---------------
For unhappy-path testing, include a header:

    X-Mock-Force-Status: 401|403|429|500|502|503

Every endpoint that respects this header will return that status. Useful
for proving the adapter's error mapping handles each case.

How to run
----------
    uv run python -m tools.mock_ats_server
    # or with custom port:
    uv run uvicorn tools.mock_ats_server:app --port 8001 --reload

Then point the adapters at it via env in apps/api/.env:

    GREENHOUSE_API_URL=http://localhost:8001/greenhouse/v1
    LEVER_API_URL=http://localhost:8001/lever/v1
    ASHBY_API_URL=http://localhost:8001/ashby

What's NOT mocked
-----------------
- OAuth flows (not relevant — we use API keys for all three)
- Webhooks (not used by the publish path)
- Job board distribution / candidate ingest (out of scope for publish)
- Greenhouse `/v1/job_posts` (we explicitly don't auto-create posts)
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s mock_ats %(message)s")
log = logging.getLogger("mock_ats")


# ── Tenant state ────────────────────────────────────────────────────────────


@dataclass
class TenantState:
    """One synthetic ATS tenant. Keyed by the API key used to access it."""

    api_key: str
    # Distinct user pool — used by every provider's on-behalf-of/owner pickers.
    users: list[dict[str, Any]] = field(default_factory=list)
    # Provider-shaped taxonomy. Same dict serves all three; each provider
    # picks the slices it cares about.
    offices: list[dict[str, Any]] = field(default_factory=list)
    departments: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    teams: list[dict[str, Any]] = field(default_factory=list)
    # Ashby keeps a separate department table because the IDs are strings
    # there while Greenhouse uses ints — mixing them breaks the wire shape.
    ashby_departments: list[dict[str, Any]] = field(default_factory=list)
    job_templates: list[dict[str, Any]] = field(default_factory=list)
    # Per-provider job ledger.
    greenhouse_jobs: list[dict[str, Any]] = field(default_factory=list)
    lever_postings: list[dict[str, Any]] = field(default_factory=list)
    ashby_openings: list[dict[str, Any]] = field(default_factory=list)


_STATE_LOCK = threading.Lock()
_STATE: dict[str, TenantState] = {}


# ── Seed ─────────────────────────────────────────────────────────────────────
# Realistic-enough data so the mapping resolver has something to fuzzy-match
# against. Every tenant starts with this; tests can mutate via the API.


_DEFAULT_USERS: list[dict[str, Any]] = [
    {"id": "1001", "name": "Aisha Patel", "email": "aisha@example.co", "primary_email_address": "aisha@example.co"},
    {"id": "1002", "name": "Marcus Chen", "email": "marcus@example.co", "primary_email_address": "marcus@example.co"},
    {"id": "1003", "name": "Sofia Rodriguez", "email": "sofia@example.co", "primary_email_address": "sofia@example.co"},
]

# Greenhouse offices wrap the location string in a nested {name: ...} object —
# match the real API shape so our adapter's parser exercises the same branch.
_DEFAULT_OFFICES: list[dict[str, Any]] = [
    {"id": 401, "name": "San Francisco HQ", "location": {"name": "San Francisco, CA"}},
    {"id": 402, "name": "New York", "location": {"name": "New York, NY"}},
    {"id": 403, "name": "Remote — North America", "location": {"name": "Remote (North America)"}},
    {"id": 404, "name": "London", "location": {"name": "London, UK"}},
    {"id": 405, "name": "Bengaluru", "location": {"name": "Bengaluru, India"}},
]

# Greenhouse departments use integer IDs.
_DEFAULT_DEPARTMENTS: list[dict[str, Any]] = [
    {"id": 501, "name": "Engineering"},
    {"id": 502, "name": "Product"},
    {"id": 503, "name": "Design"},
    {"id": 504, "name": "Go-to-Market"},
    {"id": 505, "name": "Operations"},
]

# Ashby uses opaque string IDs (UUIDs in reality). We derive deterministic
# string ids from the names so the seed is stable across restarts.
_DEFAULT_LOCATIONS: list[dict[str, Any]] = [
    {"id": "loc_" + uuid.uuid5(uuid.NAMESPACE_DNS, o["name"]).hex[:12], "name": o["name"]}
    for o in _DEFAULT_OFFICES
]

_DEFAULT_ASHBY_DEPARTMENTS: list[dict[str, Any]] = [
    {"id": "dept_" + uuid.uuid5(uuid.NAMESPACE_DNS, d["name"]).hex[:12], "name": d["name"]}
    for d in _DEFAULT_DEPARTMENTS
]

_DEFAULT_TEAMS: list[dict[str, Any]] = [
    {"id": "team_" + uuid.uuid5(uuid.NAMESPACE_DNS, d["name"]).hex[:12], "name": d["name"]}
    for d in _DEFAULT_DEPARTMENTS
]

_DEFAULT_JOB_TEMPLATES: list[dict[str, Any]] = [
    {"id": "tmpl_engineering", "name": "Generic Engineering"},
    {"id": "tmpl_product", "name": "Product Manager"},
    {"id": "tmpl_design", "name": "Designer"},
    {"id": "tmpl_gtm", "name": "Go-to-Market"},
]


def _seed_tenant(api_key: str) -> TenantState:
    """Create a fresh tenant with the default taxonomy."""
    return TenantState(
        api_key=api_key,
        users=[dict(u) for u in _DEFAULT_USERS],
        offices=[dict(o) for o in _DEFAULT_OFFICES],
        departments=[dict(d) for d in _DEFAULT_DEPARTMENTS],
        locations=[dict(l) for l in _DEFAULT_LOCATIONS],
        teams=[dict(t) for t in _DEFAULT_TEAMS],
        ashby_departments=[dict(d) for d in _DEFAULT_ASHBY_DEPARTMENTS],
        job_templates=[dict(t) for t in _DEFAULT_JOB_TEMPLATES],
    )


def _tenant(api_key: str) -> TenantState:
    """Resolve a tenant, creating one with seed data on first access."""
    with _STATE_LOCK:
        if api_key not in _STATE:
            _STATE[api_key] = _seed_tenant(api_key)
            log.info("seeded tenant key_prefix=%s", api_key[:8])
        return _STATE[api_key]


# ── Auth ────────────────────────────────────────────────────────────────────


def _extract_api_key(authorization: str | None) -> str:
    """All three providers use Basic auth, api_key as username, blank pwd.
    Parse it once here so endpoint handlers don't repeat the dance."""
    if not authorization:
        raise HTTPException(
            status_code=401, detail={"message": "Missing Authorization header"}
        )
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "basic" or not value:
        raise HTTPException(
            status_code=401, detail={"message": "Authorization must be Basic"}
        )
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except Exception as exc:
        raise HTTPException(
            status_code=401, detail={"message": "Bad Basic auth value"}
        ) from exc
    user, _, _password = decoded.partition(":")
    if not user.strip():
        raise HTTPException(
            status_code=401, detail={"message": "API key (username) required"}
        )
    return user.strip()


def _maybe_inject_error(force_status: str | None) -> None:
    """If the client sent X-Mock-Force-Status, raise that status before doing
    anything else. Lets tests prove the adapter's error-mapping branches.
    """
    if not force_status:
        return
    try:
        code = int(force_status)
    except ValueError:
        return
    if code < 400 or code >= 600:
        return
    raise HTTPException(status_code=code, detail={"message": f"forced_{code}"})


# ── FastAPI app ─────────────────────────────────────────────────────────────


app = FastAPI(
    title="NirnayaIQ Mock ATS",
    description=(
        "Stand-in for Greenhouse Harvest, Lever Postings, Ashby Public API. "
        "Dev/CI use only — see module docstring for setup."
    ),
    docs_url="/__docs",
    redoc_url=None,
)


@app.middleware("http")
async def _log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = datetime.now(UTC)
    response = await call_next(request)
    dur = (datetime.now(UTC) - started).total_seconds() * 1000
    log.info(
        "%s %s -> %s (%dms)",
        request.method,
        request.url.path,
        response.status_code,
        int(dur),
    )
    return response


# ── Operational ─────────────────────────────────────────────────────────────


@app.get("/__health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "tenants": len(_STATE),
        "started_at_utc": datetime.now(UTC).isoformat(),
    }


@app.post("/__reset")
def reset() -> dict[str, Any]:
    """Wipe every tenant's state. Test isolation hook."""
    with _STATE_LOCK:
        n = len(_STATE)
        _STATE.clear()
    log.info("reset wiped=%d", n)
    return {"ok": True, "wiped": n}


@app.get("/__inspect/{api_key}")
def inspect(api_key: str) -> dict[str, Any]:
    t = _tenant(api_key)
    return {
        "users": len(t.users),
        "offices": len(t.offices),
        "departments": len(t.departments),
        "locations": len(t.locations),
        "teams": len(t.teams),
        "job_templates": len(t.job_templates),
        "greenhouse_jobs": [
            {"id": j["id"], "name": j.get("name")} for j in t.greenhouse_jobs
        ],
        "lever_postings": [
            {"id": p["id"], "text": p.get("text")} for p in t.lever_postings
        ],
        "ashby_openings": [
            {"id": o["id"], "title": o.get("title")} for o in t.ashby_openings
        ],
    }


# ── Greenhouse Harvest API ──────────────────────────────────────────────────


class _GreenhouseJobBody(BaseModel):
    name: str = Field(max_length=255)
    notes: str | None = None
    requisition_id: str | None = None
    office_ids: list[int] | None = None
    department_id: int | None = None
    # Greenhouse accepts more fields; we ignore everything we don't echo.


@app.get("/greenhouse/v1/users")
def greenhouse_users(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    per_page: int = 100,
) -> list[dict[str, Any]]:
    """List users — also used by `test_connection()`."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _tenant(api_key).users[:per_page]


@app.get("/greenhouse/v1/offices")
def greenhouse_offices(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _tenant(api_key).offices


@app.get("/greenhouse/v1/departments")
def greenhouse_departments(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _tenant(api_key).departments


@app.post("/greenhouse/v1/jobs")
def greenhouse_create_job(
    body: _GreenhouseJobBody,
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    on_behalf_of: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)

    new_id = 70_000 + len(tenant.greenhouse_jobs) + 1
    job = {
        "id": new_id,
        "name": body.name,
        "notes": body.notes,
        "office_ids": body.office_ids or [],
        "department_id": body.department_id,
        "requisition_id": body.requisition_id,
        "created_at": datetime.now(UTC).isoformat(),
        "openings": [{"id": new_id * 10, "opening_id": str(new_id), "status": "open"}],
        "on_behalf_of_user_id": on_behalf_of,
    }
    tenant.greenhouse_jobs.append(job)
    return job


# ── Lever Postings API ──────────────────────────────────────────────────────


class _LeverPostingBody(BaseModel):
    text: str = Field(max_length=255)
    state: str = "draft"
    content: dict[str, Any] | None = None
    categories: dict[str, Any] | None = None
    user: str | None = None


@app.get("/lever/v1/users")
def lever_users(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    limit: int = 100,
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    # Lever wraps lists in {data: [...]}.
    return {"data": _tenant(api_key).users[:limit]}


@app.get("/lever/v1/postings")
def lever_postings(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    limit: int = 100,
    include: str | None = None,
) -> dict[str, Any]:
    """Used by our `list_locations` / `list_teams` distinct-category derivation."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)
    # Synthesize one posting per office × department so the distinct sets
    # the adapter derives are deterministic and non-empty even on a fresh
    # tenant.
    synthesized: list[dict[str, Any]] = []
    for office in tenant.offices[:5]:
        for dept in tenant.departments[:5]:
            synthesized.append(
                {
                    "id": str(uuid.uuid4()),
                    "text": f"Sample posting — {dept['name']}",
                    "state": "published",
                    "categories": {
                        "location": office["name"],
                        "team": dept["name"],
                    },
                }
            )
    # Plus any real postings the tenant created via POST /postings.
    return {"data": (tenant.lever_postings + synthesized)[:limit]}


@app.post("/lever/v1/postings")
def lever_create_posting(
    body: _LeverPostingBody,
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    perform_as: str | None = None,
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)

    posting_id = str(uuid.uuid4())
    posting = {
        "id": posting_id,
        "text": body.text,
        "state": body.state,
        "content": body.content or {},
        "categories": body.categories or {},
        "user": body.user or perform_as,
        "createdAt": int(datetime.now(UTC).timestamp() * 1000),
        "urls": {
            "show": f"https://hire.lever.co/postings/{posting_id}",
            "list": "https://hire.lever.co/postings",
            "apply": f"https://jobs.lever.co/example/{posting_id}",
        },
    }
    tenant.lever_postings.append(posting)
    return {"data": posting}


# ── Ashby Public API ────────────────────────────────────────────────────────


class _AshbyOpeningCreate(BaseModel):
    jobTemplateId: str | None = None
    teamId: str | None = None
    title: str = Field(max_length=255)
    description: str | None = None
    locationIds: list[str] | None = None
    departmentId: str | None = None
    hiringTeam: list[dict[str, Any]] | None = None


def _ashby_envelope(results: Any) -> dict[str, Any]:
    return {"success": True, "results": results}


@app.post("/ashby/user.list")
def ashby_user_list(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _ashby_envelope(_tenant(api_key).users)


@app.post("/ashby/location.list")
def ashby_location_list(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _ashby_envelope(_tenant(api_key).locations)


@app.post("/ashby/department.list")
def ashby_department_list(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _ashby_envelope(_tenant(api_key).ashby_departments)


@app.post("/ashby/team.list")
def ashby_team_list(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _ashby_envelope(_tenant(api_key).teams)


@app.post("/ashby/jobTemplate.list")
def ashby_job_template_list(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    return _ashby_envelope(_tenant(api_key).job_templates)


@app.post("/ashby/jobOpening.create")
def ashby_job_opening_create(
    body: _AshbyOpeningCreate,
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)

    # Ashby validates jobTemplateId server-side; mirror that so callers who
    # forget the field get the same shape error as in prod.
    if not body.jobTemplateId:
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "errors": ["jobTemplateId is required"],
            },
        )

    opening_id = str(uuid.uuid4())
    opening = {
        "id": opening_id,
        "title": body.title,
        "description": body.description,
        "jobTemplateId": body.jobTemplateId,
        "teamId": body.teamId,
        "departmentId": body.departmentId,
        "locationIds": body.locationIds or [],
        "hiringTeam": body.hiringTeam or [],
        "createdAt": datetime.now(UTC).isoformat(),
    }
    tenant.ashby_openings.append(opening)
    return _ashby_envelope(opening)


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    """`python -m tools.mock_ats_server` entrypoint."""
    import uvicorn

    port = int(os.environ.get("MOCK_ATS_PORT", "8001"))
    log.info("mock ATS server starting on :%d", port)
    uvicorn.run("tools.mock_ats_server:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
