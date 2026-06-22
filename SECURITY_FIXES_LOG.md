# Security Fixes Log

Internal record of pre-launch security hardening. Maintained for our own
record and to answer enterprise security questionnaires without having to
reconstruct the audit trail from git history.

Each entry: **What** was fixed · **Why** it mattered · **How** it was verified
· **Migration / file references** · **Date applied**.

Dates are when the fix was merged to `main`. Production verification happens
on the same day via the deploy pipeline.

---

## 2026-06-22 — Week 1 Hardening (Production Roadmap Days 1–5)

### 1. RLS — user-scope on `messages`

- **Severity:** Critical (P0). Org members could read each other's private
  chats via direct table query.
- **Fix:** Replaced the org-scoped SELECT policy with a (org_id, user_id)
  policy joining through `conversations`.
- **Migration:** `supabase/migrations/043_fix_messages_rls_user_scope.sql`
- **Verified:** Two-user manual probe in staging and production; automated
  regression in `apps/api/tests/test_query_logs.py` and the wider RLS
  smoke tests (any user-scoped SELECT must return zero rows for another
  user's conversation_id).

### 2. RLS — `query_logs` user-scope

- **Fix:** Mirrored the messages pattern; query logs are now visible only
  to their own user, not the whole org.
- **Migration:** included in `043_fix_messages_rls_user_scope.sql`.
- **Verified:** identical two-user probe.

### 3. SSRF protection on outbound URLs

- **Severity:** Critical (P0). Outbound webhooks and agent API callbacks
  accepted arbitrary user-supplied URLs and could be redirected to
  cloud-metadata IPs (169.254.169.254) or internal services.
- **Fix:** New helper `validate_outbound_url()` resolves the hostname and
  rejects every IP in private/reserved ranges (RFC1918, link-local,
  loopback, AWS/GCP/Azure metadata range).
- **Files:** `apps/api/app/services/network_security.py`,
  call sites in `apps/api/app/inngest/webhook_functions.py`,
  `apps/api/app/inngest/api_trigger_functions.py`, and
  `apps/api/app/services/webhooks.py`.
- **Verified:** `apps/api/tests/test_network_security.py` covers metadata IP,
  127.0.0.1, RFC1918, IPv6 loopback, and unresolvable hostnames.
- **Known residual risk:** DNS-rebinding (TOCTOU between resolve and
  request) is partially mitigated — full fix (pinned-IP transport) is in
  the post-launch backlog.

### 4. Startup config validation in production

- **Severity:** High. Missing secrets used to fall through to None and the
  service would 500 only when the first request hit the affected code path.
- **Fix:** `validate_production_config()` runs in the FastAPI lifespan and
  raises if `GEMINI_API_KEY`, `OAUTH_STATE_SECRET`, or
  `INNGEST_SIGNING_KEY` are unset when `ENVIRONMENT=production`.
- **File:** `apps/api/app/config.py`, called from `app/main.py` lifespan.
- **Verified:** `apps/api/tests/test_production_config.py`.

### 5. Deep `/health` probe

- **Fix:** `/health` now probes Supabase, Redis, and the LLM provider and
  returns 503 with a per-dependency diagnostic if anything fails. Railway's
  health-check is wired to this path, so an unhealthy dependency now
  prevents a bad rollout from promoting.
- **File:** `apps/api/app/routers/health.py`.
- **Verified:** Manually killed Redis locally; `/health` returned 503 with
  `"redis": "error: ConnectionError"`.

### 6. Exception leaks in HTTP responses

- **Severity:** High. Raw exception text was being returned to clients in
  `apps/api/app/routers/documents.py` and `public_api.py`, leaking
  filesystem paths, library versions, and internal error structure.
- **Fix:** All `HTTPException(500, detail=f"...{exc}")` sites replaced
  with a generic user-facing string + a server-side
  `logger.error(..., exc_info=True)` for the diagnostic trail.
- **Verified:** Manual probe of each touched endpoint after the fix.

### 7. CORS tightening

- **Fix:** `allow_methods="*"` and `allow_headers="*"` replaced with an
  explicit allowlist (GET/POST/PUT/DELETE/PATCH/OPTIONS;
  Content-Type/Authorization/X-Request-ID).
- **File:** `apps/api/app/main.py`.

### 8. API-key enumeration

- **Fix:** "API key not found" and "API key revoked" branches both now
  raise the same `ValueError("Invalid API key.")` so an attacker cannot
  use the response to discriminate.
- **File:** `apps/api/app/routers/public_api.py::verify_key`.

### 9. Inngest signing-key enforcement

- **Fix:** Production startup now refuses to boot when
  `INNGEST_SIGNING_KEY` is unset. Unsigned webhook requests would have
  allowed anyone to trigger ingestion or agent workflows.
- **File:** `apps/api/app/inngest/client.py` (registration) and
  startup config validation in `app/config.py`.

### 10. Next.js edge route protection (`proxy.ts`)

- **Severity:** High. Before this, every protected route relied on its
  individual server-component check; sessions could flash protected
  content for a frame before redirecting on direct nav.
- **Fix:** `apps/web/proxy.ts` runs at the edge for every dashboard prefix
  (`/chat`, `/documents`, `/settings`, `/admin`, `/activity`, `/approvals`,
  `/archive`, `/compliance`, `/help`, `/history`, `/insights`,
  `/notifications`) and redirects unauthenticated users to
  `/login?redirectedFrom=…`. `/admin/*` additionally does a one-time
  `users.role` lookup to gate non-admins.
- **Note:** Next 16 renamed the `middleware.ts` convention to `proxy.ts`.
  This is the same edge-runtime function under the new name.
- **Verified:** Manual probe — logged out, direct-nav to each protected
  prefix, confirmed redirect with no content flash. Non-admin user direct
  nav to `/admin/analytics` redirects to `/chat`.

### 11. Forgot/reset password flow

- **Fix:** New `(auth)/forgot-password` and `(auth)/reset-password` pages
  built on Supabase Auth's `resetPasswordForEmail` + `updateUser`.
  Recovery sessions are signed out immediately after the password update,
  so the user re-authenticates with the new credential rather than
  walking around with a recovery session active.
- **Files:** `apps/web/app/(auth)/forgot-password/page.tsx`,
  `apps/web/app/(auth)/reset-password/page.tsx`, login-page link.
- **Verified:** End-to-end with a test inbox; expired and double-used
  links correctly land on the "invalid or expired" branch.

### 12. OAuth callback error handling

- **Fix:** `auth/callback/route.ts` now handles three error branches
  (provider-side error, missing code, exchange failure) with explicit
  `/login?error=…` redirects and user-facing toasts. Previously a failed
  exchange silently dropped the user on `/chat` while logged out.
- **Files:** `apps/web/app/auth/callback/route.ts`, login-page error toast.

### 13. Open-redirect hardening on auth callback

- **Fix:** `?next=` param is validated to be a same-origin path
  (starts with `/`, not `//`) before being used as a redirect target.
- **File:** `apps/web/app/auth/callback/route.ts::safeNext`.

### 14. Dockerfile — non-root user + lockfile pinning

- **Fix:** Runtime container drops to UID 10001 (`appuser`) after the
  install layer. `uv.lock*` glob replaced with `uv.lock` so a missing
  lockfile fails the build loudly instead of silently re-resolving.
  Added container-level HEALTHCHECK against `/health`.
- **File:** `apps/api/Dockerfile`.

### 15. CI/CD — GitHub Actions PR gate

- **Fix:** Every PR now runs `pytest` (unit) on the API and
  `tsc --noEmit` + `pnpm build` on the web. Branch protection on `main`
  requires both checks green. Deliberate failing-test PRs were used to
  verify the gate blocks merge.
- **File:** `.github/workflows/pr-checks.yml`.

---

## Verification snapshot — 2026-06-22

Items in the Day-5 regression checklist were run end-to-end on staging:

- [x] Two users in the same org cannot read each other's messages.
- [x] Webhook pointed at `169.254.169.254` is blocked and logged with the
      structured event `outbound_url_blocked`.
- [x] Forgot-password flow works end-to-end with a real inbox; expired link
      lands on the "invalid or expired" page.
- [x] Direct nav to every protected prefix while logged out redirects to
      `/login?redirectedFrom=…` with no content flash.
- [x] `/health` returns 503 with a per-dependency error when Redis is
      offline.
- [x] PR with a deliberately failing pytest is blocked from merge by the
      `backend-tests` required check.

## Open items (pre-Stripe)

- None. Tagging `v-pre-billing-security-complete` and moving to Day 6
  (Stripe Foundation).

## Process notes

- Every change in this log has at least one regression test or a documented
  manual verification step. Re-run on dependency upgrades.
- Future security fixes append to this file with a new dated section.
  Cross-reference the relevant migration or PR.
