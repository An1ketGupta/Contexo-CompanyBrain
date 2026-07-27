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

Naukri (`/naukri/v1/`)
  GET  /ping                          Auth probe (Auth-Key header, NOT Basic)
  POST /jobposting                    Create / publish a HotVacancy job
  GET  /taxonomy/functionalAreas      Master list of Functional Areas (Indian taxonomy)
  GET  /taxonomy/roleCategories       Master list of Role Categories
  GET  /taxonomy/industries           Master list of Industry Types

Operational
  GET  /__health           Liveness
  POST /__reset            Wipe all tenant state (CI use)
  GET  /__inspect/{key}    Dump a tenant's jobs/taxonomy (debug)

Auth model
----------
Greenhouse / Lever / Ashby use HTTP Basic, API key as the username. Naukri
diverges and uses an `Auth-Key` header. Either way: any non-empty key is
accepted — this is a *mock*, not a security boundary. Each distinct key gets
its own isolated tenant store, so two parallel CI runs with different keys
won't collide.

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
    # Naukri's taxonomy is Indian-market specific and lives separately from
    # the ATS taxonomy — its values (e.g. "IT-Software / Software Services")
    # don't overlap with Greenhouse's departments.
    naukri_functional_areas: list[dict[str, Any]] = field(default_factory=list)
    naukri_role_categories: list[dict[str, Any]] = field(default_factory=list)
    naukri_industries: list[dict[str, Any]] = field(default_factory=list)
    # Per-provider job ledger.
    greenhouse_jobs: list[dict[str, Any]] = field(default_factory=list)
    lever_postings: list[dict[str, Any]] = field(default_factory=list)
    ashby_openings: list[dict[str, Any]] = field(default_factory=list)
    naukri_postings: list[dict[str, Any]] = field(default_factory=list)
    # Per-job candidate buckets so each provider's candidate-fetch endpoint
    # returns a deterministic, non-empty pool right after job creation. The
    # key shape is (provider, job_id) → list[candidate dict in provider shape].
    candidates: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)


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

# ── Naukri Indian-market taxonomy ───────────────────────────────────────────
# Values mirror the real Naukri taxonomy a recruiter sees in the HotVacancy
# UI. IDs are stable string codes — Naukri uses opaque numeric codes in
# practice, but stable strings make the mock easier to fixture against.

_DEFAULT_NAUKRI_FUNCTIONAL_AREAS: list[dict[str, Any]] = [
    {"id": "fa_it_software", "name": "IT-Software / Software Services"},
    {"id": "fa_sales", "name": "Sales / Business Development"},
    {"id": "fa_hr", "name": "HR / Recruitment / Administration"},
    {"id": "fa_marketing", "name": "Marketing / Advertising / MR / PR"},
    {"id": "fa_finance", "name": "Accounts / Finance / Tax / Audit"},
    {"id": "fa_design", "name": "Design / Creative / User Experience"},
    {"id": "fa_operations", "name": "Operations / Customer Service"},
    {"id": "fa_engineering", "name": "Engineering Design / R&D"},
    {"id": "fa_data_science", "name": "Analytics & Business Intelligence"},
    {"id": "fa_product", "name": "Product Management / IT"},
]

_DEFAULT_NAUKRI_ROLE_CATEGORIES: list[dict[str, Any]] = [
    {"id": "rc_programming_design", "name": "Programming & Design"},
    {"id": "rc_qa_testing", "name": "Quality Assurance / Testing"},
    {"id": "rc_devops", "name": "DevOps / Site Reliability Engineering"},
    {"id": "rc_data_engineering", "name": "Data Engineering"},
    {"id": "rc_data_science", "name": "Data Science / ML"},
    {"id": "rc_product_mgmt", "name": "Product Management"},
    {"id": "rc_field_sales", "name": "Enterprise & B2B Sales"},
    {"id": "rc_inside_sales", "name": "Inside Sales / Telesales"},
    {"id": "rc_recruitment", "name": "Recruitment / Talent Acquisition"},
    {"id": "rc_ux_design", "name": "User Experience Design (UX)"},
    {"id": "rc_visual_design", "name": "Graphic / Visual / Web Design"},
    {"id": "rc_digital_marketing", "name": "Digital Marketing / SEM / SEO"},
    {"id": "rc_brand_marketing", "name": "Brand Marketing"},
    {"id": "rc_finance_controlling", "name": "Finance & Controllership"},
    {"id": "rc_customer_success", "name": "Customer Success / Account Management"},
]

# ── Synthetic candidate pool ────────────────────────────────────────────────
# Used to seed each tenant with a few candidates per created job so the
# /candidates endpoints return something interesting on the first sync.
# Names + companies are deliberately mixed-locale so the Notion DB stays
# readable across the three providers' shape conversions.
_CANDIDATE_POOL: list[dict[str, Any]] = [
    {"first": "Priya", "last": "Sharma", "email": "priya.sharma@example.in", "company": "Flipkart", "title": "Senior Backend Engineer", "phone": "+91-98765-43210", "stage": "Phone Screen"},
    {"first": "Daniel", "last": "Müller", "email": "daniel.mueller@example.de", "company": "SAP", "title": "Staff Engineer", "phone": "+49-89-12345678", "stage": "Onsite"},
    {"first": "Aiko", "last": "Tanaka", "email": "aiko@example.jp", "company": "Mercari", "title": "Platform Engineer", "phone": "+81-3-1234-5678", "stage": "Take-home"},
    {"first": "Emma", "last": "Johnson", "email": "emma.j@example.com", "company": "Stripe", "title": "Senior SWE", "phone": "+1-415-555-0181", "stage": "Phone Screen"},
    {"first": "Rahul", "last": "Iyer", "email": "rahul.iyer@example.in", "company": "Razorpay", "title": "Backend Lead", "phone": "+91-98111-22334", "stage": "Offer"},
    {"first": "Sofía", "last": "García", "email": "sofia.g@example.es", "company": "Glovo", "title": "Senior Engineer", "phone": "+34-91-1234567", "stage": "Application Review"},
    {"first": "Liam", "last": "O'Connor", "email": "liam@example.ie", "company": "Stripe Ireland", "title": "Engineering Manager", "phone": "+353-1-1234567", "stage": "Onsite"},
]


_DEFAULT_NAUKRI_INDUSTRIES: list[dict[str, Any]] = [
    {"id": "ind_it_services", "name": "IT-Software / Software Services"},
    {"id": "ind_internet", "name": "Internet / E-commerce"},
    {"id": "ind_banking", "name": "Banking / Financial Services / Broking"},
    {"id": "ind_insurance", "name": "Insurance"},
    {"id": "ind_bpo", "name": "BPO / Call Centre / ITES"},
    {"id": "ind_education", "name": "Education / Teaching / Training"},
    {"id": "ind_healthcare", "name": "Medical / Healthcare / Hospitals"},
    {"id": "ind_pharma", "name": "Pharma / Biotech / Clinical Research"},
    {"id": "ind_retail", "name": "Retail / Wholesale"},
    {"id": "ind_consulting", "name": "Consulting / Strategy / Operations"},
    {"id": "ind_media", "name": "Media / Entertainment / Internet"},
    {"id": "ind_real_estate", "name": "Real Estate / Property"},
    {"id": "ind_telecom", "name": "Telecom / ISP"},
    {"id": "ind_automobile", "name": "Automobile / Auto Anciliary / Auto Components"},
    {"id": "ind_food", "name": "Food Processing / FMCG"},
]


def _seed_candidates_for_job(
    tenant: TenantState, provider: str, job_id: str, count: int = 5
) -> None:
    """Stash a deterministic slice of the candidate pool against this job.

    Different providers get the same person under different wire shapes —
    this is what makes the cross-platform sync test believable. Candidate
    ids are namespaced by provider so the same pool entry has distinct
    external ids per ATS (the sync layer keys upserts on that).
    """
    pool = _CANDIDATE_POOL[:count]
    bucket: list[dict[str, Any]] = []
    for i, c in enumerate(pool):
        cid = f"{provider[:2]}-{job_id}-{i + 1:03d}"
        applied = datetime.now(UTC).isoformat()
        if provider == "greenhouse":
            bucket.append({
                "id": int(cid.replace("-", "")[-9:]) if cid.replace("-", "")[-9:].isdigit() else (10_000 + i),
                "first_name": c["first"],
                "last_name": c["last"],
                "company": c["company"],
                "title": c["title"],
                "email_addresses": [{"type": "personal", "value": c["email"]}],
                "phone_numbers": [{"type": "mobile", "value": c["phone"]}],
                "applications": [
                    {
                        "id": int(cid.replace("-", "")[-9:]) if cid.replace("-", "")[-9:].isdigit() else (20_000 + i),
                        "candidate_id": int(cid.replace("-", "")[-9:]) if cid.replace("-", "")[-9:].isdigit() else (10_000 + i),
                        "current_stage": {"id": i + 1, "name": c["stage"]},
                        "applied_at": applied,
                        "status": "active",
                    }
                ],
                "attachments": [
                    {"filename": "resume.pdf", "type": "resume", "url": f"https://files.example.com/resume-{cid}.pdf"}
                ],
            })
        elif provider == "lever":
            bucket.append({
                "id": cid,
                "name": f"{c['first']} {c['last']}",
                "headline": c["title"],
                "emails": [c["email"]],
                "phones": [{"type": "mobile", "value": c["phone"]}],
                "organizations": [c["company"]],
                "stage": {"id": f"stage-{i+1}", "text": c["stage"]},
                "createdAt": int(datetime.now(UTC).timestamp() * 1000),
                "urls": {"show": f"https://hire.lever.co/candidates/{cid}"},
            })
        elif provider == "ashby":
            bucket.append({
                "id": cid,
                "createdAt": applied,
                "currentInterviewStage": {"id": f"is-{i+1}", "title": c["stage"]},
                "candidateId": cid,
                "candidate": {
                    "id": cid,
                    "name": f"{c['first']} {c['last']}",
                    "company": c["company"],
                    "position": c["title"],
                    "emailAddresses": [{"isPrimary": True, "value": c["email"]}],
                    "phoneNumbers": [{"isPrimary": True, "value": c["phone"]}],
                    "resumeFileHandle": f"https://files.example.com/ashby-resume-{cid}.pdf",
                },
            })
    tenant.candidates[(provider, str(job_id))] = bucket


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
        naukri_functional_areas=[dict(x) for x in _DEFAULT_NAUKRI_FUNCTIONAL_AREAS],
        naukri_role_categories=[dict(x) for x in _DEFAULT_NAUKRI_ROLE_CATEGORIES],
        naukri_industries=[dict(x) for x in _DEFAULT_NAUKRI_INDUSTRIES],
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
    title="Contexo Mock ATS",
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
        "naukri_functional_areas": len(t.naukri_functional_areas),
        "naukri_role_categories": len(t.naukri_role_categories),
        "naukri_industries": len(t.naukri_industries),
        "greenhouse_jobs": [
            {"id": j["id"], "name": j.get("name")} for j in t.greenhouse_jobs
        ],
        "lever_postings": [
            {"id": p["id"], "text": p.get("text")} for p in t.lever_postings
        ],
        "ashby_openings": [
            {"id": o["id"], "title": o.get("title")} for o in t.ashby_openings
        ],
        "naukri_postings": [
            {"id": p["id"], "title": p.get("jobTitle")} for p in t.naukri_postings
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
    _seed_candidates_for_job(tenant, "greenhouse", str(new_id))
    return job


@app.get("/greenhouse/v1/candidates")
def greenhouse_candidates(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    job_id: str | None = None,
    per_page: int = 100,
    page: int = 1,
) -> list[dict[str, Any]]:
    """GET /v1/candidates?job_id=… — used by the candidate-sync flow."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)
    if not job_id:
        return []
    bucket = tenant.candidates.get(("greenhouse", str(job_id))) or []
    start = max(0, (page - 1) * per_page)
    return bucket[start : start + per_page]


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
    _seed_candidates_for_job(tenant, "lever", posting_id)
    return {"data": posting}


@app.get("/lever/v1/opportunities")
def lever_opportunities(
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
    posting_id: str | None = None,
    limit: int = 100,
    expand: str | None = None,
) -> dict[str, Any]:
    """GET /v1/opportunities?posting_id=… for the candidate-sync flow."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)
    if not posting_id:
        return {"data": []}
    bucket = tenant.candidates.get(("lever", str(posting_id))) or []
    return {"data": bucket[:limit]}


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


@app.post("/ashby/application.list")
def ashby_application_list(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    """POST /application.list filtered by jobId — candidate-sync source."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_api_key(authorization)
    tenant = _tenant(api_key)
    job_id = (payload or {}).get("jobId")
    if not job_id:
        return _ashby_envelope([])
    bucket = tenant.candidates.get(("ashby", str(job_id))) or []
    limit = int((payload or {}).get("limit") or 100)
    return _ashby_envelope({"data": bucket[:limit], "moreDataAvailable": False})


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
    _seed_candidates_for_job(tenant, "ashby", opening_id)
    return _ashby_envelope(opening)


# ── Naukri HotVacancy API ───────────────────────────────────────────────────
# Naukri uses Auth-Key (not Basic). Separate helper so we don't accidentally
# accept Basic auth on a Naukri endpoint — which would mask a real-world
# mismatch where our adapter sent the wrong header style.


def _extract_naukri_key(auth_key: str | None) -> str:
    """Naukri's Auth-Key header carries the raw API key (no scheme prefix)."""
    if not auth_key or not auth_key.strip():
        raise HTTPException(
            status_code=401,
            detail={"message": "Missing Auth-Key header"},
        )
    return auth_key.strip()


class _NaukriJobBody(BaseModel):
    """Mirror of the documented Naukri HotVacancy create payload.

    Extra fields are accepted silently because Naukri's real API ignores
    unknowns rather than 400-ing — easier for partners to ship without
    waiting on the docs to catch up. We keep the same forgiveness here.
    """

    jobTitle: str = Field(max_length=255)
    jobDescription: str | None = None
    jobLocation: str = Field(max_length=255)
    department: str | None = None
    functionalAreaId: str | None = None
    roleCategoryId: str | None = None
    industryTypeId: str | None = None
    minExperience: int | None = Field(default=None, ge=0, le=40)
    maxExperience: int | None = Field(default=None, ge=0, le=40)
    keySkills: list[str] | None = None
    salary: str | None = None
    hideSalary: bool | None = None

    model_config = {"extra": "allow"}


@app.get("/naukri/v1/ping")
def naukri_ping(
    auth_key: str | None = Header(default=None, alias="Auth-Key"),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    """Connectivity + auth probe used by `naukri.test_connection()`."""
    _maybe_inject_error(x_mock_force_status)
    _extract_naukri_key(auth_key)
    return {"ok": True, "service": "naukri-mock", "version": "v1"}


@app.get("/naukri/v1/taxonomy/functionalAreas")
def naukri_functional_areas(
    auth_key: str | None = Header(default=None, alias="Auth-Key"),
    x_mock_force_status: str | None = Header(default=None),
    page: int = 1,
    pageSize: int = 200,
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_naukri_key(auth_key)
    items = _tenant(api_key).naukri_functional_areas
    start = max(0, (page - 1) * pageSize)
    return {"data": items[start : start + pageSize], "totalCount": len(items)}


@app.get("/naukri/v1/taxonomy/roleCategories")
def naukri_role_categories(
    auth_key: str | None = Header(default=None, alias="Auth-Key"),
    x_mock_force_status: str | None = Header(default=None),
    page: int = 1,
    pageSize: int = 200,
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_naukri_key(auth_key)
    items = _tenant(api_key).naukri_role_categories
    start = max(0, (page - 1) * pageSize)
    return {"data": items[start : start + pageSize], "totalCount": len(items)}


@app.get("/naukri/v1/taxonomy/industries")
def naukri_industries(
    auth_key: str | None = Header(default=None, alias="Auth-Key"),
    x_mock_force_status: str | None = Header(default=None),
    page: int = 1,
    pageSize: int = 200,
) -> dict[str, Any]:
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_naukri_key(auth_key)
    items = _tenant(api_key).naukri_industries
    start = max(0, (page - 1) * pageSize)
    return {"data": items[start : start + pageSize], "totalCount": len(items)}


@app.post("/naukri/v1/jobposting")
def naukri_create_posting(
    body: _NaukriJobBody,
    auth_key: str | None = Header(default=None, alias="Auth-Key"),
    account_id: str | None = Header(default=None, alias="Account-Id"),
    x_mock_force_status: str | None = Header(default=None),
) -> dict[str, Any]:
    """Mirror Naukri's create endpoint shape: returns `jobId` + `postingUrl`."""
    _maybe_inject_error(x_mock_force_status)
    api_key = _extract_naukri_key(auth_key)
    tenant = _tenant(api_key)

    # Naukri validates that minExperience <= maxExperience before creating.
    # Mirror so our adapter's swap-then-send branch is exercised by the test.
    if (
        body.minExperience is not None
        and body.maxExperience is not None
        and body.maxExperience < body.minExperience
    ):
        return JSONResponse(
            status_code=400,
            content={
                "errorCode": "invalid_experience_band",
                "message": "maxExperience must be >= minExperience",
            },
        )

    posting_id = 900_000 + len(tenant.naukri_postings) + 1
    posting = {
        "id": posting_id,
        "jobId": str(posting_id),
        "jobTitle": body.jobTitle,
        "jobDescription": body.jobDescription,
        "jobLocation": body.jobLocation,
        "department": body.department,
        "functionalAreaId": body.functionalAreaId,
        "roleCategoryId": body.roleCategoryId,
        "industryTypeId": body.industryTypeId,
        "minExperience": body.minExperience,
        "maxExperience": body.maxExperience,
        "keySkills": body.keySkills or [],
        "salary": body.salary,
        "hideSalary": body.hideSalary,
        "accountId": account_id,
        "createdAt": datetime.now(UTC).isoformat(),
        "postingUrl": f"https://employer.naukri.com/jobpostings/{posting_id}",
        "candidateUrl": f"https://www.naukri.com/job-listings-{posting_id}",
        "status": "ACTIVE",
    }
    tenant.naukri_postings.append(posting)
    return posting


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    """`python -m tools.mock_ats_server` entrypoint."""
    import uvicorn

    port = int(os.environ.get("MOCK_ATS_PORT", "8001"))
    log.info("mock ATS server starting on :%d", port)
    uvicorn.run("tools.mock_ats_server:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
