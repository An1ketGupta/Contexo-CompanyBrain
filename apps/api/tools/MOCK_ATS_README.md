# Mock ATS Server

Stand-in for **Greenhouse Harvest**, **Lever Postings**, **Ashby Public**, and **Naukri HotVacancy** APIs.

All four providers are enterprise sales-only — there's no self-serve developer tier we can use to exercise the publish loop end-to-end in dev or CI. This server mimics all four accurately enough that our adapters' `test_connection()`, `list_*()`, and `publish_job()` paths succeed without modification.

Naukri sits alongside the ATSes in this mock even though it's a job board (not an internal hiring system). The connect / publish wire shape is the same enough that one mock covers both kinds.

## Run it (the easy way)

Two-line setup. In `apps/api/.env`:

```ini
USE_MOCK_ATS=true
MOCK_ATS_URL=http://localhost:8001
```

```bash
# Terminal 1 — start the mock
cd apps/api
uv run python -m tools.mock_ats_server
# Listens on http://localhost:8001

# Terminal 2 — restart FastAPI so the flag is picked up
cd apps/api
uv run uvicorn app.main:app --reload
```

On boot you'll see a banner:
```
WARNING: USE_MOCK_ATS=true — Greenhouse/Lever/Ashby calls go to http://localhost:8001
```

That's the signal all three adapters now route to the mock automatically. **No per-provider URL env vars needed.**

### Granular overrides (advanced)

If you want to mock *some* providers but hit real APIs for others, leave `USE_MOCK_ATS=false` and set individual URLs:

```ini
GREENHOUSE_API_URL=http://localhost:8001/greenhouse/v1
# LEVER_API_URL stays unset → real api.lever.co
# ASHBY_API_URL stays unset → real api.ashbyhq.com
```

`USE_MOCK_ATS=true` always wins over per-provider URLs.

## Smoke test it

```bash
# Terminal 2 — verify wire compatibility
cd apps/api
uv run python -m tools.smoke_test_ats
```

Exits non-zero on any failure. Covers:

1. **Wire format** — direct HTTP calls to the mock matching each provider's documented shape
2. **Error injection** — `X-Mock-Force-Status: 503` actually surfaces
3. **Adapter integration** — calling `greenhouse.test_connection()`, `ashby.list_locations()`, etc. via the real adapter modules (with env overrides) reaches the mock and returns the expected types

A clean run prints:

```
[OK] All ATS smoke tests passed.
```

## End-to-end demo (full product loop against mock)

1. Mock running on `:8001`, env vars set, FastAPI restarted
2. In NirnayaIQ web UI: **Settings → Integrations → Greenhouse (or Lever / Ashby) → Connect**
3. Paste **any non-empty string** as the API key (mock accepts everything — it's not a security boundary)
4. Backend calls the mock's `test_connection` endpoint → 200 → credential stored → mapping cache warmed
5. **Recruiting → New requisition** → fill role / location / department / seniority → generate
6. Pick a JD variant → choose your ATS → mapping preview shows `high` confidence matches against the seeded taxonomy (San Francisco HQ, Engineering, etc.)
7. **Publish to ATS** → backend hits the mock's create endpoint → 200 → audit log row written, hiring-manager email enqueued, requisition marked `published`
8. Check the mock's inspector: `curl http://localhost:8001/__inspect/<your-api-key>` shows the job persisted

## Auth model

Basic auth, API key as the username, blank password — same as all three real providers. **Any non-empty key is accepted.** Each distinct key gets its own isolated tenant ledger (taxonomy + jobs), so two parallel CI runs with different keys won't collide.

## Endpoints reference

### Greenhouse — `/greenhouse/v1/`

| Method | Path | Purpose |
|---|---|---|
| GET | `/users` | Auth probe + on-behalf-of picker |
| POST | `/jobs` | Create job |
| GET | `/offices` | Taxonomy |
| GET | `/departments` | Taxonomy |

### Lever — `/lever/v1/`

| Method | Path | Purpose |
|---|---|---|
| GET | `/users` | Auth probe + posting owner picker |
| GET | `/postings` | Returns synthetic + real postings for category derivation |
| POST | `/postings` | Create posting |

### Ashby — `/ashby/`

| Method | Path | Purpose |
|---|---|---|
| POST | `/user.list` | Auth probe + hiring team picker |
| POST | `/jobOpening.create` | Create job opening (requires `jobTemplateId`) |
| POST | `/location.list` | Taxonomy |
| POST | `/department.list` | Taxonomy |
| POST | `/team.list` | Taxonomy |
| POST | `/jobTemplate.list` | Taxonomy |

### Naukri — `/naukri/v1/`

Auth diverges from the ATSes: Naukri uses an **`Auth-Key`** header (NOT HTTP Basic).
Multi-account contracts also send an `Account-Id` header.

| Method | Path | Purpose |
|---|---|---|
| GET | `/ping` | Auth probe (cheap connectivity check) |
| POST | `/jobposting` | Create / publish a HotVacancy job |
| GET | `/taxonomy/functionalAreas` | Indian-market Functional Area master list |
| GET | `/taxonomy/roleCategories` | Role Category master list |
| GET | `/taxonomy/industries` | Industry Type master list |

### Operational

| Method | Path | Purpose |
|---|---|---|
| GET | `/__health` | Liveness — returns tenant count + start time |
| POST | `/__reset` | Wipe every tenant's state (test isolation) |
| GET | `/__inspect/{api_key}` | Dump a tenant's jobs/taxonomy |
| GET | `/__docs` | OpenAPI / Swagger UI |

## Error injection

For unhappy-path testing, include a header:

```
X-Mock-Force-Status: 401
X-Mock-Force-Status: 403
X-Mock-Force-Status: 429
X-Mock-Force-Status: 500
X-Mock-Force-Status: 502
X-Mock-Force-Status: 503
```

Every endpoint respects this header and short-circuits with the requested status. Useful for proving each adapter's error-mapping branch:

- `401` → `PermissionError("X_unauthorized")`
- `403` → `PermissionError("X_forbidden")`
- `5xx` → `RuntimeError("X_publish_failed")`

To force a specific call to fail, you'd need to temporarily edit the adapter or proxy to inject the header. The smoke test injects via direct HTTP for the Greenhouse `/users` 503 case.

## Seed data

Every tenant boots with realistic data so the mapping resolver has something non-trivial to fuzzy-match against:

- **5 offices** (Greenhouse) — SF HQ, NY, Remote NA, London, Bengaluru
- **5 departments** — Engineering, Product, Design, Go-to-Market, Operations
- **5 locations** (Ashby) — deterministic UUIDs derived from office names
- **5 teams** (Ashby) — deterministic UUIDs derived from department names
- **4 job templates** (Ashby) — Engineering, Product Manager, Designer, Go-to-Market
- **3 users** — Aisha Patel, Marcus Chen, Sofia Rodriguez
- **10 functional areas** (Naukri) — IT-Software, Sales, HR, Marketing, Finance, Design, Operations, Engineering, Analytics, Product Management
- **15 role categories** (Naukri) — Programming, QA, DevOps, Data Engineering, Data Science, Product Mgmt, Field Sales, Inside Sales, Recruitment, UX, Visual Design, Digital Marketing, Brand Marketing, Finance, Customer Success
- **15 industries** (Naukri) — IT-Software, Internet/E-commerce, Banking, Insurance, BPO, Education, Healthcare, Pharma, Retail, Consulting, Media, Real Estate, Telecom, Automobile, FMCG

State persists for the lifetime of the process. Hit `POST /__reset` between test runs for isolation.

## What's NOT mocked

- **OAuth flows** — not used; all three real providers also offer API-key auth, which is what we picked
- **Webhooks** — out of scope for the publish path
- **Job board distribution / candidate ingest** — we only do "create job + capture URL", not the full hiring lifecycle
- **Greenhouse `/v1/job_posts`** — we explicitly do not auto-create posts (the recruiter does that inside Greenhouse where the templating logic lives)
- **Rate limits** — the mock won't 429 you on volume; it only 429s when you ask via the force header

## When the mock is NOT enough

The mock validates **wire format** — request shapes, response shapes, auth, status codes. Things it can't validate that you'll only hit against a real provider:

1. **Provider-side data validation** — e.g. Ashby requires a real `jobTemplateId` that exists in your account; the mock just checks "is the field present"
2. **OAuth scope requirements** — N/A for API-key auth
3. **Rate limits and backoff behavior** at production volume
4. **Field length / format constraints** beyond what's in the docs (some providers reject titles longer than the documented 255 chars in practice)

When a real customer connects with a real API key, expect a 5-10 minute round of "real Greenhouse rejects this exact payload because X" — every integration has this phase. The mock just makes sure that phase is the *only* one, not "your code never worked at all."

## File map

```
apps/api/tools/
├── __init__.py                Package marker
├── mock_ats_server.py         The FastAPI mock app
├── smoke_test_ats.py          End-to-end smoke test (CI-friendly)
└── MOCK_ATS_README.md         This file
```

## Architecture note

The mock is intentionally a **single file** with no shared abstraction between providers, even though their endpoints look similar. The whole point is that each provider's wire format is captured *as-it-actually-is*, not in some normalized internal form. If real Ashby changes a field shape, you fix one place in this file and the smoke test catches the mismatch on the next CI run.

State lives in a process-local dict guarded by a single `threading.Lock`. This is fine — the mock is not production, will never see real concurrency that benefits from finer locking, and the simplicity is worth more than the theoretical perf gain. Don't refactor it into Redis or SQLite. If you need persistence across restarts for some test, hit `POST /__reset` at the start of your suite and rely on per-tenant seeding.
