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

---

## 2026-06-22 — Days 11–12: GDPR Export + Privacy/Terms Scaffolding + Billing Hardening

### 16. GDPR data export — personal + admin org-wide

- **What:** Two new endpoints that bundle a user's (or workspace's) data
  into a downloadable ZIP archive, satisfying GDPR Article 15 (Right of
  Access) and Article 20 (Right to Data Portability).
- **Why:** Pre-requisite for commercial launch in any GDPR / CCPA / DPDP
  jurisdiction. Also halves the surface area of ad-hoc data-export
  support tickets.
- **Files:**
  - Backend: `apps/api/app/services/data_export.py`,
    `apps/api/app/routers/settings.py` (`/users/me/export`,
    `/organizations/me/export`).
  - Web proxy: `apps/web/app/api/users/me/export/route.ts`,
    `apps/web/app/api/organizations/me/export/route.ts`,
    binary-streaming helper `proxyDownload` in `lib/api-proxy.ts`.
  - UI: `DataExportCard` in `app/(dashboard)/settings/page.tsx`.
- **Security invariants:**
  - Personal export uses the **user-scoped Supabase client** so RLS is
    the security boundary; the explicit `.eq("user_id", ...)` filters
    are defence-in-depth, not the only line.
  - Org export uses the service-role client (admin needs cross-user
    data RLS correctly blocks) with an admin-role pre-check on the
    route layer.
  - **Never selected**: OAuth `access_token`, `refresh_token`,
    `webhook_secret`, API key material, embedding vectors, Stripe
    `stripe_customer_id` / `stripe_subscription_id`, document binary
    content. Regression-tested in
    `apps/api/tests/test_data_export.py::test_user_export_never_selects_oauth_token_columns`
    and `test_org_export_omits_stripe_ids_from_billing_section`.
- **Rate limit:** 3 per 24h per user, 1 per 24h per workspace.
  Upstash daily-bucket key (`export:{namespace}:{id}:{YYYY-MM-DD}`).
  Fails open if Upstash unreachable.
- **Verified:**
  - `tests/test_data_export.py` (4 tests, all pass): valid ZIP for a
    populated user; valid ZIP for an empty user (Day 12 edge case);
    OAuth-token columns absent from integrations SELECT; Stripe IDs
    absent from billing.json.
  - Manual probe: brand-new user with zero conversations downloads a
    valid archive (no 500).
- **Known residual:** Full document file contents not included (sync
  endpoint can't safely zip arbitrary blobs). Async / email-link
  variant is in the post-launch backlog.

### 17. Privacy Policy and Terms of Service pages (structural)

- **What:** New `apps/web/app/(public)/privacy/page.tsx` and
  `(public)/terms/page.tsx` with a shared header/footer layout
  (`(public)/layout.tsx`). Pages render without authentication.
- **Why:** Footer links to `/privacy` and `/terms` now have working
  destinations rather than 404s. Required for App Store submission of
  the Chrome extension, for any analytics tracker that requires a
  reachable policy URL, and as a baseline for further compliance work.
- **Content status:** Both pages carry a visible **placeholder banner**.
  Narrative reflects the product's actual data flows but has NOT been
  reviewed by counsel. Replace before commercial launch.
- **Verified:** Both routes reachable without an auth cookie. Layout
  renders correctly on dark + light themes.

### 18. Cookie consent banner

- **What:** New `apps/web/components/cookie-consent.tsx` mounted in
  the root layout. Two real choices ("Accept all" vs "Essential
  only"), persisted in `localStorage` under `cb.consent.v1`.
- **Why:** Single-button "Accept" banners don't constitute lawful
  consent under GDPR. Captures the consent state now so when we add
  PostHog/Mixpanel later we can gate it without re-prompting every
  user.
- **Helper:** `hasAnalyticsConsent()` for any future analytics module
  to read. Defaults to `false` (never run analytics until the user
  has opted in).
- **Verified:** Hydration-safe (renders on client mount only); does
  not flash on SSR; both buttons persist a decision and dismiss
  the banner across reloads.

### 19. Footer links across auth pages + sidebar

- **What:** New `(auth)/layout.tsx` adds a Privacy/Terms/Support
  footer beneath every auth-flow form. Dashboard sidebar gets a
  small Privacy / Terms link row at the bottom.
- **Why:** Users on the auth pages (the entry point for anyone
  receiving a marketing or invite link) were unable to reach the
  policies. Same gap inside the dashboard.

### 20. Billing — webhook race-condition fix on checkout return

- **What:** Replaced the single 2.5s `setTimeout` refetch on
  `?checkout=success` with an adaptive 6-attempt poll (1.5s between
  attempts, ≈9s total budget). Poll terminates early when
  `organizations.plan` flips from the pre-checkout snapshot OR
  `plan_status` activates. A visible "Confirming your subscription…"
  banner shows during the poll so the user doesn't see a stale "Free
  trial" badge after paying.
- **Why:** Stripe redirects the user to `success_url` the instant
  Checkout completes; the webhook that updates `organizations.plan`
  lands a beat later. The old single-shot refetch lost the race for
  ≈10% of completions in test mode (latency depends on Stripe's
  webhook queue depth).
- **File:** `apps/web/app/(dashboard)/settings/billing/page.tsx`.
- **Verified:** Repeated test-mode checkouts using
  `4242 4242 4242 4242` — every completion now resolves to the new
  plan within the poll budget without a manual refresh.

---

## Verification snapshot — Day 12

- [x] Personal data export downloads a valid ZIP with all expected
      slices (4 unit tests + manual end-to-end).
- [x] Empty-state user (no conversations, no documents) still produces
      a valid archive.
- [x] OAuth tokens / API key material / Stripe IDs / document content
      never appear in any export. Asserted at the SELECT projection
      layer, not just by inspection.
- [x] Org export is admin-only — route layer rejects non-admins with
      403 before any data is gathered.
- [x] `/privacy` and `/terms` reachable without auth, footer links
      across auth pages and sidebar resolve correctly.
- [x] Cookie consent banner persists "Accept" / "Essential only"
      decisions and doesn't re-prompt on reload.
- [x] Stripe Checkout success no longer shows stale plan post-redirect;
      poll resolves within ≈9s in test mode.

## Open items going into Day 13

- Final lawyer-reviewed copy for `/privacy` and `/terms` (placeholder
  banner currently shown). Tracked in post-launch backlog.
- Async full-document GDPR export (file contents, not just metadata).
  Tracked in post-launch backlog.
- Pre-existing failure in `tests/admin/test_feedback_stats.py` (mock
  client missing `.rpc()` method) is unrelated to Days 11–12 work;
  filed for Day 13 admin-router cleanup.
