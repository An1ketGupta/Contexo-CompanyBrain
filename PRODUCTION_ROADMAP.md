# NirnayaIQ — 14-Day Production Readiness Roadmap

**Goal:** Take NirnayaIQ from 48/100 to commercially launchable in 14 working days.
**Scope:** All P0 launch blockers + full Stripe billing + four P1 bundles (security quick-wins, upload/rate limiting, GDPR + legal scaffolding, invite/role/password UX).
**Explicitly deferred to post-launch backlog:** JWT/session caching, connection pooling, all P2/P3 items not listed below.

---

## Confirmed Architecture & Tech Stack Decisions

These are locked in before implementation. If any of these are wrong, stop and correct before Day 1.

| Decision | Choice | Rationale |
|---|---|---|
| **Billing checkout flow** | **Stripe Checkout (hosted)** | Zero PCI scope, no card-handling code to write or secure, fastest path to a working upgrade flow. Stripe Elements would add 3-4 days of embedded-form work (validation states, SCA handling, error UI) for a benefit (in-app branding) that doesn't matter at this stage. |
| **Plan sync model** | **Webhook-driven** | `organizations.plan` is updated only by Stripe webhook events (`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`). Stripe is the single source of truth. No polling, no client-side plan assumptions. |
| **SSRF validator** | **Pure Python, stdlib only** (`socket`, `ipaddress`) | No new dependency to audit or pin. The check is simple enough (resolve hostname → check resulting IP against private/reserved ranges) that a library adds risk (supply chain, staleness) without meaningfully reducing code. |
| **CI/CD** | **GitHub Actions, PR gates only** | `pytest -m "not integration"`, `tsc --noEmit`, `pnpm build` run on every PR. No Railway deploy-gating integration — Railway continues to auto-deploy on merge to `main`, but nothing merges to `main` without green checks. This is the 80/20: most production breakage comes from untested code reaching `main`, not from Railway's deploy mechanics. |
| **JWT/session caching** | **Deferred to post-launch** | Not a launch blocker. Current per-request Supabase auth call is acceptable below ~100 concurrent users. Tracked in backlog section at the bottom of this doc. |
| **Connection pooling** | **Deferred to post-launch** | Same rationale. Tracked in backlog. |
| **In-scope P1 bundle 1** | Inngest signing key enforcement, exception-leak fixes, CORS tightening, API key timing fix | ~5 hours total, bundled into Day 1 alongside other security fixes since they touch the same files/mental model. |
| **In-scope P1 bundle 2** | Document upload plan limits, rate limiting (upload init/complete, OAuth callbacks, admin aggregation endpoints) | Bundled into Days 9-10. |
| **In-scope P1 bundle 3** | GDPR data export endpoint, Privacy Policy + ToS *pages* (technical scaffolding — routing, static rendering, cookie banner) | Legal *copy* is explicitly out of scope — you need an actual lawyer or a service like Termly/Iubenda for the text itself. The roadmap builds the page, the route, the cookie consent banner, and a placeholder content slot. Bundled into Days 11-12. |
| **In-scope P1 bundle 4** | Invite flow fix for existing users, member role promotion UI + endpoint, in-app password change | Bundled into Days 8-9. |
| **Database migration strategy** | **One additive SQL migration per logical change, numbered sequentially, never edit a past migration** | Matches your existing 41-migration history. Each Day that touches the DB gets its own migration file. |
| **Stripe plan mapping** | **3 Stripe Price IDs (Starter / Team / Business), stored in `organizations.stripe_subscription_id`, `organizations.stripe_customer_id`, `organizations.plan`, `organizations.plan_status`** | Plan name and status are denormalized onto `organizations` for fast reads on every request (quota checks happen on the hot path). Stripe remains the source of truth; this is a cache of it, rebuilt by webhook. |
| **Rate limiting backend** | **Redis (already in stack via existing chat rate limiter)** | Reuse the existing Redis rate-limit pattern from the chat endpoint rather than introducing a new mechanism (e.g., in-memory or DB-based limits). |
| **Feature flag for new endpoints during rollout** | **None — direct ship** | Given the 14-day compressed timeline, no flag infrastructure is introduced. Each fix is merged via the new CI gate and is correct-by-construction or it doesn't merge. |

**⚠️ Please confirm or correct before I proceed to write any code from this roadmap:**
1. Confirm your Stripe account already exists (or will be created Day 6) and you have at least Starter/Team/Business pricing tiers decided in dollar terms.
2. Confirm `organizations` table is the correct table to denormalize plan info onto (not a separate `billing` or `subscriptions` table).
3. Confirm Redis is already provisioned and reachable from the API (used today by the chat rate limiter) — Day 9-10 rate limiting reuses this connection.
4. Confirm you want Privacy Policy/ToS as static content you'll paste in later (placeholder approach) rather than blocking Day 11-12 on having final legal text ready.

---

## Day-by-Day Plan

### **Day 1 — Security Quick Wins + RLS Fix (P0 + P1 Bundle 1)**

**Morning block (RLS — highest severity item in the entire doc):**

1. Open `supabase/migrations/` and create new file `042_fix_messages_rls_user_scope.sql`.
2. Write the migration:
   ```sql
   -- 042_fix_messages_rls_user_scope.sql
   -- Fixes CRITICAL privacy bug: org members can read each other's private chats.
   BEGIN;

   DROP POLICY IF EXISTS messages_select_policy ON messages;

   CREATE POLICY messages_select_policy ON messages
     FOR SELECT
     USING (
       org_id = auth_org_id()
       AND conversation_id IN (
         SELECT id FROM conversations WHERE user_id = auth.uid()
       )
     );

   COMMIT;
   ```
3. **Before applying to production**, apply to a local/staging Supabase instance first:
   ```bash
   supabase db reset --local
   supabase migration up --local
   ```
4. Write a manual verification test: create two test users (`user_a`, `user_b`) in the same org, each with one conversation. Authenticate as `user_a`, query `messages` table directly via the Supabase client, confirm zero rows from `user_b`'s conversation are returned.
5. Write an automated regression test in `apps/api/tests/test_rls_messages.py`:
   ```python
   def test_user_cannot_read_other_users_messages(supabase_client_user_a, supabase_client_user_b, seeded_conversations):
       # user_b's conversation_id is known from seed data
       result = supabase_client_user_a.table("messages").select("*").eq(
           "conversation_id", seeded_conversations["user_b_conversation_id"]
       ).execute()
       assert len(result.data) == 0
   ```
6. Apply migration to production via Supabase CLI: `supabase db push`.
7. Immediately re-run the manual verification test (step 4) against production with real (or disposable test) accounts to confirm the fix is live.
8. **Also check and fix `query_logs` RLS in the same sitting** (Technical Debt item #9 / P1 "Confirm query_logs RLS policy"): run `SELECT * FROM pg_policies WHERE tablename = 'query_logs';` in the Supabase SQL editor. If no SELECT policy scoped to `user_id` exists, write migration `043_query_logs_rls.sql` mirroring the same pattern as messages.

**Afternoon block (P1 Bundle 1 — quick security wins, ~5 hours):**

9. **Inngest signing key enforcement** — open `apps/api/app/inngest/client.py`. Find the line wrapping signing key usage in `if settings.inngest_signing_key:`. Add to `apps/api/app/main.py` inside the `lifespan` context manager (or wherever startup checks live):
   ```python
   if settings.environment == "production" and not settings.inngest_signing_key:
       raise RuntimeError(
           "INNGEST_SIGNING_KEY is required in production. "
           "Unsigned requests would allow anyone to trigger ingestion/agent workflows."
       )
   ```
10. **Exception leak fixes** — open `apps/api/app/routers/documents.py`, go to line 187 and line 293. Replace:
    ```python
    raise HTTPException(status_code=500, detail=f"Failed to process document: {exc}")
    ```
    with:
    ```python
    logger.error(f"Document processing failed: {exc}", exc_info=True)
    raise HTTPException(status_code=500, detail="Failed to process document. Please try again or contact support.")
    ```
    Repeat the same pattern for `apps/api/app/routers/public_api.py` lines 81, 296, and 498 — each gets a fixed, generic client-facing string and a server-side `logger.error(..., exc_info=True)` call. Use distinct generic messages per failure type (upload, retrieval, agent execution) so support tickets remain diagnosable without leaking internals.
11. **CORS tightening** — open `apps/api/app/main.py` lines 82-84. Replace:
    ```python
    allow_methods=["*"],
    allow_headers=["*"],
    ```
    with:
    ```python
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    ```
    Grep the frontend codebase for any custom header usage (`grep -rn "headers:" apps/web/src` ) to confirm no header is missed before tightening — add any found headers to the allowlist.
12. **API key enumeration fix** — open `apps/api/app/routers/public_api.py`, find `verify_key()`. Locate both raise sites:
    ```python
    raise ValueError("API key not found")
    raise ValueError("API key revoked")
    ```
    Replace both with the identical:
    ```python
    raise ValueError("Invalid API key.")
    ```
    Confirm the calling code that catches this `ValueError` and converts to a 401 response does not leak which branch was hit (check for any differing status codes or response timing — if the revoked-key path does an extra DB write/log before raising, make sure it doesn't introduce a measurable timing difference; keep both paths doing the same minimal work before the identical raise).
13. Run full test suite locally: `pytest apps/api/tests/ -m "not integration"`.
14. Commit with message: `fix(security): RLS user-scoping, exception leaks, CORS, API key enumeration, Inngest signing enforcement`.

**End of Day 1 checklist:**
- [ ] Messages RLS fixed and verified in production
- [ ] query_logs RLS confirmed/fixed
- [ ] Inngest signing key startup assertion live
- [ ] All 5 exception leak sites fixed
- [ ] CORS allowlist tightened
- [ ] API key enumeration unified
- [ ] All changes covered by at least one regression test
- [ ] Deployed to production and smoke-tested

---

### **Day 2 — SSRF Protection + Startup Config Validation**

**Morning block (SSRF — second highest severity item):**

1. Create new file `apps/api/app/core/network_security.py`:
   ```python
   import ipaddress
   import socket
   from urllib.parse import urlparse

   PRIVATE_RANGES = [
       ipaddress.ip_network("10.0.0.0/8"),
       ipaddress.ip_network("172.16.0.0/12"),
       ipaddress.ip_network("192.168.0.0/16"),
       ipaddress.ip_network("127.0.0.0/8"),
       ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP/Azure metadata endpoint range
       ipaddress.ip_network("::1/128"),
       ipaddress.ip_network("fc00::/7"),
       ipaddress.ip_network("fe80::/10"),
       ipaddress.ip_network("0.0.0.0/8"),
   ]

   class UnsafeURLError(Exception):
       pass

   def validate_outbound_url(url: str) -> None:
       """Raises UnsafeURLError if the URL resolves to a private/reserved IP.
       Must be called immediately before every outbound httpx call to a
       user-supplied URL (webhooks, agent callbacks)."""
       parsed = urlparse(url)
       if parsed.scheme not in ("http", "https"):
           raise UnsafeURLError(f"Unsupported scheme: {parsed.scheme}")
       if not parsed.hostname:
           raise UnsafeURLError("URL has no hostname")

       try:
           resolved_ips = socket.getaddrinfo(parsed.hostname, None)
       except socket.gaierror as exc:
           raise UnsafeURLError(f"Could not resolve hostname: {parsed.hostname}") from exc

       for family, _, _, _, sockaddr in resolved_ips:
           ip_str = sockaddr[0]
           ip = ipaddress.ip_address(ip_str)
           for private_range in PRIVATE_RANGES:
               if ip in private_range:
                   raise UnsafeURLError(
                       f"URL resolves to a private/reserved IP ({ip_str}). Blocked."
                   )
   ```
2. **Important edge case to handle:** DNS rebinding (a hostname that resolves to a public IP at validation time but a private IP at request time). Mitigate by resolving the IP once in `validate_outbound_url`, then passing the resolved IP directly to httpx instead of the hostname, OR by re-validating immediately before the actual request fires (acceptable for this timeline — full rebinding protection via pinned-IP requests is a post-launch hardening item, note it in Technical Debt).
3. Write unit tests in `apps/api/tests/test_network_security.py`:
   ```python
   def test_blocks_aws_metadata_ip():
       with pytest.raises(UnsafeURLError):
           validate_outbound_url("http://169.254.169.254/latest/meta-data/")

   def test_blocks_localhost():
       with pytest.raises(UnsafeURLError):
           validate_outbound_url("http://127.0.0.1:8000/admin")

   def test_blocks_private_range():
       with pytest.raises(UnsafeURLError):
           validate_outbound_url("http://192.168.1.1/")

   def test_allows_public_url():
       validate_outbound_url("https://hooks.slack.com/services/xxx")  # should not raise

   def test_blocks_unresolvable_hostname():
       with pytest.raises(UnsafeURLError):
           validate_outbound_url("http://this-domain-does-not-exist-xyz123.com/")
   ```
4. Open `apps/api/app/inngest/webhook_functions.py`. Find every `httpx.post(...)`/`httpx.request(...)` call that sends to a user-supplied URL. Immediately before each call, add:
   ```python
   from app.core.network_security import validate_outbound_url, UnsafeURLError
   try:
       validate_outbound_url(webhook_url)
   except UnsafeURLError as exc:
       logger.warning(f"Blocked SSRF attempt: {exc}")
       # mark delivery as failed in the webhook_deliveries table, do not raise to caller in a way that confirms internal network structure
       return WebhookDeliveryResult(status="blocked", reason="invalid_destination")
   ```
5. Repeat identically for `apps/api/app/inngest/api_trigger_functions.py` for agent API callbacks.
6. Grep for any other location making outbound calls to stored/user-supplied URLs: `grep -rn "httpx\." apps/api/app/ | grep -v test_`. Confirm no other call site (e.g., integration sync jobs hitting third-party callback URLs) was missed.
7. Run `pytest apps/api/tests/test_network_security.py -v` and the full webhook function test suite.

**Afternoon block (startup config validation + deep health check):**

8. Open `apps/api/app/core/config.py` (or wherever `Settings` is defined). Add a new function:
   ```python
   def validate_production_config(settings: Settings) -> None:
       if settings.environment != "production":
           return
       required = {
           "GEMINI_API_KEY": settings.gemini_api_key,
           "OAUTH_STATE_SECRET": settings.oauth_state_secret,
           "INNGEST_SIGNING_KEY": settings.inngest_signing_key,
       }
       missing = [name for name, value in required.items() if not value]
       if missing:
           raise RuntimeError(
               f"Missing required production env vars: {', '.join(missing)}"
           )
   ```
9. Call `validate_production_config(settings)` inside the `lifespan` startup block in `main.py`, before the app starts accepting requests.
10. **Deep health check** — open the existing `GET /health` route. Replace the trivial "process is alive" check with dependency probes:
    ```python
    @router.get("/health")
    async def health_check():
        checks = {}
        overall_ok = True

        try:
            supabase_client.table("organizations").select("id").limit(1).execute()
            checks["supabase"] = "ok"
        except Exception as exc:
            checks["supabase"] = f"error: {type(exc).__name__}"
            overall_ok = False

        try:
            redis_client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
            overall_ok = False

        try:
            # lightweight reachability check, not a full generation call
            await gemini_client.models.list()  # or equivalent cheap call
            checks["llm"] = "ok"
        except Exception as exc:
            checks["llm"] = f"error: {type(exc).__name__}"
            overall_ok = False

        status_code = 200 if overall_ok else 503
        return JSONResponse(status_code=status_code, content={"status": "ok" if overall_ok else "degraded", "checks": checks})
    ```
11. Confirm Railway's health check configuration points at `/health` and treats 503 as "deploy unhealthy, do not promote" (check Railway service settings → Health Check path/timeout).
12. Test by temporarily setting a wrong Redis URL locally and confirming `/health` returns 503 with `"redis": "error: ..."`.
13. Commit: `fix(security): SSRF protection on webhook/agent callbacks, startup config validation, deep health checks`.

**End of Day 2 checklist:**
- [ ] `validate_outbound_url` implemented, tested, wired into both webhook and agent callback paths
- [ ] DNS rebinding edge case documented (mitigated, not fully solved — noted for post-launch)
- [ ] Production startup fails loudly if critical env vars are missing
- [ ] `/health` probes Supabase, Redis, and LLM reachability and returns 503 on failure
- [ ] Railway health check config confirmed pointing at `/health`

---

### **Day 3 — Forgot Password Flow + Next.js Route Protection Middleware**

**Morning block (forgot password — Day-1 support ticket generator):**

1. Create `apps/web/src/app/forgot-password/page.tsx`:
   ```tsx
   "use client";
   import { useState } from "react";
   import { createClient } from "@/lib/supabase/client";

   export default function ForgotPasswordPage() {
     const [email, setEmail] = useState("");
     const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
     const supabase = createClient();

     async function handleSubmit(e: React.FormEvent) {
       e.preventDefault();
       setStatus("sending");
       const { error } = await supabase.auth.resetPasswordForEmail(email, {
         redirectTo: `${window.location.origin}/reset-password`,
       });
       setStatus(error ? "error" : "sent");
     }

     return (
       <div className="max-w-md mx-auto mt-24 px-4">
         <h1 className="text-2xl font-semibold mb-4">Reset your password</h1>
         {status === "sent" ? (
           <p>Check your email for a password reset link.</p>
         ) : (
           <form onSubmit={handleSubmit} className="space-y-4">
             <input
               type="email"
               required
               value={email}
               onChange={(e) => setEmail(e.target.value)}
               placeholder="you@company.com"
               className="w-full border rounded px-3 py-2"
             />
             <button type="submit" disabled={status === "sending"} className="w-full bg-primary text-white rounded px-3 py-2">
               {status === "sending" ? "Sending..." : "Send reset link"}
             </button>
             {status === "error" && <p className="text-red-600 text-sm">Something went wrong. Try again.</p>}
           </form>
         )}
       </div>
     );
   }
   ```
2. Create `apps/web/src/app/reset-password/page.tsx` (the page the email link lands on — Supabase appends a session token to the URL fragment automatically):
   ```tsx
   "use client";
   import { useState } from "react";
   import { useRouter } from "next/navigation";
   import { createClient } from "@/lib/supabase/client";

   export default function ResetPasswordPage() {
     const [password, setPassword] = useState("");
     const [confirmPassword, setConfirmPassword] = useState("");
     const [status, setStatus] = useState<"idle" | "saving" | "error">("idle");
     const [errorMsg, setErrorMsg] = useState("");
     const router = useRouter();
     const supabase = createClient();

     async function handleSubmit(e: React.FormEvent) {
       e.preventDefault();
       if (password.length < 8) {
         setErrorMsg("Password must be at least 8 characters.");
         setStatus("error");
         return;
       }
       if (password !== confirmPassword) {
         setErrorMsg("Passwords do not match.");
         setStatus("error");
         return;
       }
       setStatus("saving");
       const { error } = await supabase.auth.updateUser({ password });
       if (error) {
         setErrorMsg(error.message);
         setStatus("error");
         return;
       }
       router.push("/login?reset=success");
     }

     return (
       <div className="max-w-md mx-auto mt-24 px-4">
         <h1 className="text-2xl font-semibold mb-4">Set a new password</h1>
         <form onSubmit={handleSubmit} className="space-y-4">
           <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="New password" className="w-full border rounded px-3 py-2" />
           <input type="password" required minLength={8} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Confirm new password" className="w-full border rounded px-3 py-2" />
           <button type="submit" disabled={status === "saving"} className="w-full bg-primary text-white rounded px-3 py-2">
             {status === "saving" ? "Saving..." : "Update password"}
           </button>
           {status === "error" && <p className="text-red-600 text-sm">{errorMsg}</p>}
         </form>
       </div>
     );
   }
   ```
3. Open `apps/web/src/app/login/page.tsx`. Add a link below the password field: `<a href="/forgot-password" className="text-sm text-muted-foreground hover:underline">Forgot password?</a>`.
4. In Supabase Dashboard → Authentication → URL Configuration, confirm `{your-domain}/reset-password` is added to the **Redirect URLs allowlist** — Supabase rejects `resetPasswordForEmail` redirects to unlisted URLs.
5. In Supabase Dashboard → Authentication → Email Templates → "Reset Password", confirm the template's action link matches the expected format and points to your domain, not the Supabase default.
6. Manually test end-to-end: trigger reset from `/forgot-password` with a real test inbox, click the email link, confirm landing on `/reset-password` with a valid session, set new password, confirm redirect to `/login?reset=success`, log in with new password.
7. Add a small toast/banner on `/login` when `?reset=success` is present: "Password updated. Please log in."

**Afternoon block (Next.js middleware — edge route protection):**

8. Create `apps/web/middleware.ts` at the project root (same level as `next.config.js`):
   ```typescript
   import { NextResponse } from "next/server";
   import type { NextRequest } from "next/server";
   import { createServerClient } from "@supabase/ssr";

   const PROTECTED_PREFIXES = [
     "/chat", "/history", "/notifications", "/insights", "/activity",
     "/approvals", "/archive", "/settings", "/admin",
   ];
   const PUBLIC_PATHS = ["/login", "/signup", "/forgot-password", "/reset-password", "/accept-invite", "/privacy", "/terms"];

   export async function middleware(request: NextRequest) {
     const { pathname } = request.nextUrl;

     const isProtected = PROTECTED_PREFIXES.some((p) => pathname.startsWith(p));
     if (!isProtected) {
       return NextResponse.next();
     }

     let response = NextResponse.next({ request });

     const supabase = createServerClient(
       process.env.NEXT_PUBLIC_SUPABASE_URL!,
       process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
       {
         cookies: {
           getAll() { return request.cookies.getAll(); },
           setAll(cookiesToSet) {
             cookiesToSet.forEach(({ name, value, options }) => request.cookies.set(name, value));
             response = NextResponse.next({ request });
             cookiesToSet.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
           },
         },
       }
     );

     const { data: { user } } = await supabase.auth.getUser();

     if (!user) {
       const redirectUrl = new URL("/login", request.url);
       redirectUrl.searchParams.set("redirectedFrom", pathname);
       return NextResponse.redirect(redirectUrl);
     }

     return response;
   }

   export const config = {
     matcher: [
       "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
     ],
   };
   ```
9. **Admin route extra check:** for any path under `/admin`, the middleware above only confirms the user is authenticated, not that they're an org admin. Add a secondary check by reading a custom claim or making a lightweight call:
   ```typescript
   if (pathname.startsWith("/admin")) {
     const { data: { user } } = await supabase.auth.getUser();
     const isAdmin = user?.app_metadata?.role === "admin" || user?.app_metadata?.role === "owner";
     if (!isAdmin) {
       return NextResponse.redirect(new URL("/chat", request.url));
     }
   }
   ```
   Confirm `app_metadata.role` is actually the field your Supabase Auth setup populates — if role lives in the `organization_members` table instead, this check needs a DB call inside middleware (acceptable, but adds latency — note in code comment that this is a deliberate tradeoff for launch).
10. Test manually: log out, hit `/chat` directly in the URL bar, confirm immediate redirect to `/login?redirectedFrom=/chat` with no flash of protected content. Repeat for `/admin/analytics` as a non-admin user.
11. Confirm existing server-component-level redirect logic (the "brief flash before redirect" behavior mentioned in the original audit) is now redundant but harmless — leave it as defense-in-depth, don't remove it.
12. Commit: `feat(auth): forgot-password/reset-password flow, edge middleware route protection`.

**End of Day 3 checklist:**
- [ ] `/forgot-password` and `/reset-password` pages live and tested end-to-end with real email
- [ ] Supabase redirect URL allowlist updated
- [ ] Login page links to forgot-password
- [ ] `middleware.ts` blocks unauthenticated access to all protected prefixes at the edge
- [ ] Admin routes have a secondary role check
- [ ] No content flash on direct navigation to protected routes while logged out

---

### **Day 4 — CI/CD Pipeline + Dockerfile Hardening + OAuth Callback Error Handling**

**Morning block (CI/CD):**

1. Create `.github/workflows/pr-checks.yml`:
   ```yaml
   name: PR Checks

   on:
     pull_request:
       branches: [main]

   jobs:
     backend-tests:
       runs-on: ubuntu-latest
       defaults:
         run:
           working-directory: apps/api
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: "3.11"
         - name: Install uv
           run: pip install uv
         - name: Install dependencies
           run: uv sync --frozen
         - name: Run pytest (unit tests only)
           run: uv run pytest -m "not integration" --maxfail=5
           env:
             ENVIRONMENT: test
             GEMINI_API_KEY: test-placeholder
             OAUTH_STATE_SECRET: test-placeholder
             INNGEST_SIGNING_KEY: test-placeholder

     frontend-typecheck-and-build:
       runs-on: ubuntu-latest
       defaults:
         run:
           working-directory: apps/web
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-node@v4
           with:
             node-version: "20"
         - uses: pnpm/action-setup@v3
           with:
             version: 9
         - name: Install dependencies
           run: pnpm install --frozen-lockfile
         - name: Type check
           run: pnpm tsc --noEmit
         - name: Build
           run: pnpm build
           env:
             NEXT_PUBLIC_SUPABASE_URL: https://placeholder.supabase.co
             NEXT_PUBLIC_SUPABASE_ANON_KEY: placeholder-key
   ```
2. Go to GitHub repo Settings → Branches → Branch protection rules → add rule for `main`:
   - Require status checks to pass before merging
   - Select `backend-tests` and `frontend-typecheck-and-build` as required checks
   - Require branches to be up to date before merging
   - (Optional but recommended) Require at least 1 approval if you ever add a second contributor
3. Open a throwaway test PR with a deliberate `pytest` failure (e.g., `assert False`) to confirm the check actually blocks merge. Confirm the red X appears and "Merge" button is disabled. Revert the deliberate failure.
4. Open a second throwaway PR with a deliberate TypeScript error to confirm `tsc --noEmit` catches it. Revert.
5. Document the CI setup briefly in `CONTRIBUTING.md` or `README.md`: what runs on PR, how to run the same checks locally before pushing.

**Afternoon block (Dockerfile hardening + small fixes):**

6. Open the Dockerfile. Find the `uv.lock*` glob (Technical Debt item #2). Fix:
   ```dockerfile
   COPY uv.lock pyproject.toml ./
   ```
7. Add non-root user before the `CMD` instruction:
   ```dockerfile
   RUN useradd -m appuser
   USER appuser
   ```
   **Important:** test this doesn't break file permission assumptions — if the app writes to any local directory (logs, temp files), confirm `appuser` has write access, or explicitly `chown` those directories before the `USER` switch:
   ```dockerfile
   RUN mkdir -p /app/tmp && chown appuser:appuser /app/tmp
   USER appuser
   ```
8. Rebuild locally: `docker build -t nirnayaiq-api:test .` and run: `docker run -p 8000:8000 nirnayaiq-api:test`. Confirm the container starts successfully and `/health` responds. Check logs for any permission-denied errors.
9. Remove the legacy `enforce_chat_rate_limit` shim (Technical Debt item #1) from `rate_limit.py` — confirm via `grep -rn "enforce_chat_rate_limit"` that it's truly unused before deleting.

**OAuth callback error handling:**

10. Open `apps/web/src/app/auth/callback/route.ts`. Current code likely looks like:
    ```typescript
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    return NextResponse.redirect(`${origin}/chat`);
    ```
    Fix:
    ```typescript
    export async function GET(request: NextRequest) {
      const { searchParams, origin } = new URL(request.url);
      const code = searchParams.get("code");

      if (!code) {
        return NextResponse.redirect(`${origin}/login?error=missing_code`);
      }

      const supabase = createServerClient(/* ... */);
      const { error } = await supabase.auth.exchangeCodeForSession(code);

      if (error) {
        console.error("OAuth callback error:", error.message);
        return NextResponse.redirect(`${origin}/login?error=oauth_failed`);
      }

      return NextResponse.redirect(`${origin}/chat`);
    }
    ```
11. On `/login` page, read the `error` query param and show a user-facing message: `"oauth_failed"` → "Sign-in failed. Please try again."; `"missing_code"` → "Sign-in link was invalid or expired."
12. Test by manually hitting `/auth/callback?code=invalid-code-xyz` and confirming a clean redirect to `/login?error=oauth_failed` with a visible message, not a silent logged-out `/chat` load.
13. Commit: `ci: add GitHub Actions PR gates; fix(docker): non-root user, lockfile glob; fix(auth): OAuth callback error handling`.

**End of Day 4 checklist:**
- [ ] GitHub Actions running pytest + tsc + build on every PR
- [ ] Branch protection on `main` requiring these checks
- [ ] Verified with deliberate failing test PRs (then reverted)
- [ ] Dockerfile runs as non-root, lockfile glob fixed
- [ ] OAuth callback handles errors with explicit redirect + user-facing message
- [ ] Legacy rate-limit shim removed

---

### **Day 5 — Buffer Day / Catch-Up + Week 1 Hardening Review**

This day is intentionally a buffer. In a 14-day compressed timeline, Days 1-4 cover dense, interdependent security work where something will likely run long (RLS testing across staging/prod, Supabase email template debugging, or a CI environment-variable mismatch are the most probable culprits). Use Day 5 as follows:

1. **If Days 1-4 finished on schedule:** pull forward and start Day 6 (Stripe setup) early.
2. **If anything slipped**, prioritize finishing in this order: RLS fix (non-negotiable, must be done) → SSRF → forgot password → middleware → CI/CD.
3. Regardless of schedule status, spend 1-2 hours on a **full P0 regression pass**:
   - [ ] Log in as two different users in the same org, confirm message isolation holds
   - [ ] Attempt a webhook pointed at `169.254.169.254`, confirm it's blocked and logged
   - [ ] Trigger forgot password, complete full reset flow
   - [ ] Log out, hit every protected route prefix directly, confirm redirect with no flash
   - [ ] Confirm `/health` returns 503 if you kill Redis locally
   - [ ] Open a PR with a failing test, confirm it's blocked from merge
4. Write a short internal `SECURITY_FIXES_LOG.md` documenting what was fixed, when, and how it was verified — useful both for your own record and if you ever need to demonstrate due diligence to an enterprise customer's security questionnaire.
5. Tag a git release/checkpoint: `git tag v-pre-billing-security-complete`.

**End of Day 5 checklist:**
- [ ] All Week 1 P0 items confirmed working in production via regression pass
- [ ] `SECURITY_FIXES_LOG.md` written
- [ ] Git checkpoint tagged

---

### **Day 6 — Stripe Foundation: Account, Products, Database Schema**

**Morning block (Stripe account + product setup):**

1. If not already done, create/confirm Stripe account at dashboard.stripe.com. Switch to **Test mode** for all work this week — do not touch Live mode until Day 14.
2. In Stripe Dashboard → Product Catalog, create three Products: `Starter`, `Team`, `Business`. For each, create a recurring monthly Price (and optionally an annual Price — confirm with yourself whether annual billing is in scope for launch; if uncertain, **ship monthly-only first**, annual is easy to add later).
3. Record the resulting Price IDs (format `price_xxx`) — you'll need these in code on Day 7. Store them in `.env` as:
   ```
   STRIPE_PRICE_STARTER_MONTHLY=price_xxx
   STRIPE_PRICE_TEAM_MONTHLY=price_xxx
   STRIPE_PRICE_BUSINESS_MONTHLY=price_xxx
   ```
4. In Stripe Dashboard → Developers → API Keys, copy the **Test** Secret Key and Publishable Key into `.env`:
   ```
   STRIPE_SECRET_KEY=sk_test_xxx
   STRIPE_PUBLISHABLE_KEY=pk_test_xxx
   ```
5. Install the Stripe SDK: `uv add stripe` in `apps/api`.
6. In Stripe Dashboard → Developers → Webhooks, add an endpoint pointing at `https://your-staging-or-prod-domain.com/api/webhooks/stripe` (you'll build this route on Day 7 — the URL just needs to exist before you can grab the signing secret, but Stripe allows adding the endpoint before it's live). Select events:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
7. Copy the **Signing secret** (`whsec_xxx`) into `.env` as `STRIPE_WEBHOOK_SECRET`.
8. Install Stripe CLI locally (`brew install stripe/stripe-cli/stripe` or equivalent) for local webhook testing: `stripe login`, then later `stripe listen --forward-to localhost:8000/api/webhooks/stripe`.

**Afternoon block (database schema):**

9. Create migration `044_billing_columns.sql`:
   ```sql
   BEGIN;

   ALTER TABLE organizations
     ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE,
     ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT UNIQUE,
     ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free',
     ADD COLUMN IF NOT EXISTS plan_status TEXT NOT NULL DEFAULT 'active',
     ADD COLUMN IF NOT EXISTS plan_updated_at TIMESTAMPTZ DEFAULT now(),
     ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ;

   CREATE INDEX IF NOT EXISTS idx_organizations_stripe_customer_id ON organizations(stripe_customer_id);

   COMMENT ON COLUMN organizations.plan IS 'free | starter | team | business — denormalized cache of Stripe subscription, rebuilt by webhook';
   COMMENT ON COLUMN organizations.plan_status IS 'active | past_due | canceled | incomplete — mirrors Stripe subscription status';

   COMMIT;
   ```
10. Create a second migration `045_billing_events_log.sql` for webhook idempotency and audit trail (critical — Stripe can send duplicate webhook events, and you need to detect and skip duplicates):
    ```sql
    BEGIN;

    CREATE TABLE IF NOT EXISTS billing_events (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      stripe_event_id TEXT NOT NULL UNIQUE,
      event_type TEXT NOT NULL,
      org_id UUID REFERENCES organizations(id),
      payload JSONB NOT NULL,
      processed_at TIMESTAMPTZ DEFAULT now(),
      created_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_billing_events_org_id ON billing_events(org_id);

    ALTER TABLE billing_events ENABLE ROW LEVEL SECURITY;
    -- No public SELECT policy — this table is service-role-only, accessed by the webhook handler and admin endpoints.

    COMMIT;
    ```
11. Apply both migrations to local/staging first, verify schema with `\d organizations` and `\d billing_events` in `psql` or Supabase SQL editor, then push to production.
12. Write a small data backfill: for all existing organizations (pre-billing), explicitly set `plan = 'free'`, `plan_status = 'active'` if not already defaulted (the `DEFAULT 'free'` in the migration handles new rows; confirm existing rows also got the default via `ADD COLUMN ... DEFAULT` semantics — Postgres applies the default to existing rows automatically for `ADD COLUMN`, so this should already be correct, but verify with `SELECT plan, count(*) FROM organizations GROUP BY plan;`).
13. Commit: `feat(billing): Stripe products configured, billing schema migrations 044-045`.

**End of Day 6 checklist:**
- [ ] Stripe test-mode account configured with 3 products/prices
- [ ] API keys and webhook secret in `.env`
- [ ] Stripe CLI installed for local testing
- [ ] `organizations` table has billing columns
- [ ] `billing_events` table exists for webhook idempotency
- [ ] Existing orgs backfilled to `plan = 'free'`

---

### **Day 7 — Stripe Checkout Session Creation + Webhook Handler**

**Morning block (Checkout session creation endpoint):**

1. Create `apps/api/app/routers/billing.py`:
   ```python
   import stripe
   from fastapi import APIRouter, Depends, HTTPException
   from app.core.config import settings
   from app.dependencies.auth import get_current_user_and_org

   stripe.api_key = settings.stripe_secret_key
   router = APIRouter(prefix="/api/billing", tags=["billing"])

   PRICE_MAP = {
       "starter": settings.stripe_price_starter_monthly,
       "team": settings.stripe_price_team_monthly,
       "business": settings.stripe_price_business_monthly,
   }

   @router.post("/checkout-session")
   async def create_checkout_session(plan: str, user_and_org = Depends(get_current_user_and_org)):
       user, org = user_and_org
       if plan not in PRICE_MAP:
           raise HTTPException(status_code=400, detail="Invalid plan selected.")

       # Only org admins/owners can initiate billing changes
       if org["role"] not in ("admin", "owner"):
           raise HTTPException(status_code=403, detail="Only org admins can manage billing.")

       customer_id = org.get("stripe_customer_id")
       if not customer_id:
           customer = stripe.Customer.create(
               email=user.email,
               metadata={"org_id": str(org["id"])},
           )
           customer_id = customer.id
           # persist immediately so retries don't create duplicate Stripe customers
           supabase_admin.table("organizations").update(
               {"stripe_customer_id": customer_id}
           ).eq("id", org["id"]).execute()

       session = stripe.checkout.Session.create(
           customer=customer_id,
           mode="subscription",
           line_items=[{"price": PRICE_MAP[plan], "quantity": 1}],
           success_url=f"{settings.frontend_url}/settings/billing?checkout=success",
           cancel_url=f"{settings.frontend_url}/settings/billing?checkout=canceled",
           client_reference_id=str(org["id"]),
           metadata={"org_id": str(org["id"]), "plan": plan},
       )
       return {"checkout_url": session.url}
   ```
2. **Critical detail:** `client_reference_id` AND `metadata.org_id` are both set — redundant but deliberate. Some webhook event types carry `client_reference_id` directly on the session object, others require digging into `metadata`. Setting both means the webhook handler never has to guess.
3. Add a billing portal endpoint for existing subscribers to manage/cancel:
   ```python
   @router.post("/portal-session")
   async def create_portal_session(user_and_org = Depends(get_current_user_and_org)):
       user, org = user_and_org
       if not org.get("stripe_customer_id"):
           raise HTTPException(status_code=400, detail="No billing account found.")
       session = stripe.billing_portal.Session.create(
           customer=org["stripe_customer_id"],
           return_url=f"{settings.frontend_url}/settings/billing",
       )
       return {"portal_url": session.url}
   ```
4. Register the router in `main.py`: `app.include_router(billing.router)`.
5. Write tests in `apps/api/tests/test_billing.py` mocking `stripe.checkout.Session.create` and `stripe.Customer.create` (use `unittest.mock.patch` — never hit real Stripe API in unit tests):
   ```python
   @patch("stripe.checkout.Session.create")
   @patch("stripe.Customer.create")
   def test_creates_checkout_session_for_new_customer(mock_customer_create, mock_session_create, ...):
       mock_customer_create.return_value = MagicMock(id="cus_test123")
       mock_session_create.return_value = MagicMock(url="https://checkout.stripe.com/test")
       response = client.post("/api/billing/checkout-session", json={"plan": "team"}, headers=auth_headers)
       assert response.status_code == 200
       assert "checkout_url" in response.json()

   def test_rejects_non_admin_from_creating_checkout():
       response = client.post("/api/billing/checkout-session", json={"plan": "team"}, headers=member_auth_headers)
       assert response.status_code == 403
   ```

**Afternoon block (webhook handler — the most important piece of the entire billing system):**

6. Create `apps/api/app/routers/webhooks_stripe.py`:
   ```python
   import stripe
   from fastapi import APIRouter, Request, HTTPException
   from app.core.config import settings
   from app.db.admin_client import supabase_admin

   router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

   PLAN_BY_PRICE_ID = {
       settings.stripe_price_starter_monthly: "starter",
       settings.stripe_price_team_monthly: "team",
       settings.stripe_price_business_monthly: "business",
   }

   @router.post("/stripe")
   async def stripe_webhook(request: Request):
       payload = await request.body()
       sig_header = request.headers.get("stripe-signature")

       try:
           event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
       except stripe.error.SignatureVerificationError:
           raise HTTPException(status_code=400, detail="Invalid signature.")
       except ValueError:
           raise HTTPException(status_code=400, detail="Invalid payload.")

       # Idempotency check — Stripe may send the same event more than once
       existing = supabase_admin.table("billing_events").select("id").eq("stripe_event_id", event["id"]).execute()
       if existing.data:
           return {"status": "already_processed"}

       event_type = event["type"]
       data = event["data"]["object"]
       org_id = None

       if event_type == "checkout.session.completed":
           org_id = data.get("client_reference_id") or data.get("metadata", {}).get("org_id")
           subscription_id = data.get("subscription")
           customer_id = data.get("customer")
           plan = data.get("metadata", {}).get("plan", "starter")
           supabase_admin.table("organizations").update({
               "stripe_subscription_id": subscription_id,
               "stripe_customer_id": customer_id,
               "plan": plan,
               "plan_status": "active",
               "plan_updated_at": "now()",
           }).eq("id", org_id).execute()

       elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
           customer_id = data.get("customer")
           status = data.get("status")  # active, past_due, canceled, incomplete, etc.
           price_id = data["items"]["data"][0]["price"]["id"] if data.get("items", {}).get("data") else None
           plan = PLAN_BY_PRICE_ID.get(price_id, "unknown")
           period_end = data.get("current_period_end")
           org_lookup = supabase_admin.table("organizations").select("id").eq("stripe_customer_id", customer_id).execute()
           if org_lookup.data:
               org_id = org_lookup.data[0]["id"]
               supabase_admin.table("organizations").update({
                   "plan": plan,
                   "plan_status": status,
                   "current_period_end": period_end,
                   "plan_updated_at": "now()",
               }).eq("id", org_id).execute()

       elif event_type == "customer.subscription.deleted":
           customer_id = data.get("customer")
           org_lookup = supabase_admin.table("organizations").select("id").eq("stripe_customer_id", customer_id).execute()
           if org_lookup.data:
               org_id = org_lookup.data[0]["id"]
               supabase_admin.table("organizations").update({
                   "plan": "free",
                   "plan_status": "canceled",
                   "plan_updated_at": "now()",
               }).eq("id", org_id).execute()

       elif event_type == "invoice.payment_failed":
           customer_id = data.get("customer")
           org_lookup = supabase_admin.table("organizations").select("id").eq("stripe_customer_id", customer_id).execute()
           if org_lookup.data:
               org_id = org_lookup.data[0]["id"]
               supabase_admin.table("organizations").update({"plan_status": "past_due"}).eq("id", org_id).execute()
               # TODO Day 13: trigger an email/notification to org admin about payment failure

       # Log every processed event for idempotency + audit trail, regardless of type
       supabase_admin.table("billing_events").insert({
           "stripe_event_id": event["id"],
           "event_type": event_type,
           "org_id": org_id,
           "payload": event,
       }).execute()

       return {"status": "success"}
   ```
7. **Critical: register this route BEFORE any global auth middleware applies to it.** Stripe webhooks are unauthenticated by design (they're verified by signature, not by your auth system) — confirm `/api/webhooks/stripe` is excluded from any `Depends(get_current_user)` chain and from CORS credential requirements. It should accept POST with no `Authorization` header.
8. **Critical: this route needs the raw request body for signature verification**, not a parsed JSON body. Confirm FastAPI's `await request.body()` gives raw bytes, not something pre-parsed by a global JSON-parsing middleware. If a global middleware reads the body first (common gotcha), the byte stream may be consumed already — test this explicitly.
9. Local webhook testing: run `stripe listen --forward-to localhost:8000/api/webhooks/stripe` in one terminal, run the API locally in another, then trigger a test event: `stripe trigger checkout.session.completed`. Confirm the event lands in `billing_events` and `organizations.plan` updates correctly.
10. Write webhook handler tests using Stripe's test event fixtures (construct a fake event dict matching Stripe's schema, bypass signature verification in test mode via dependency override):
    ```python
    def test_webhook_updates_org_plan_on_checkout_completed(mock_stripe_event_checkout_completed):
        response = client.post("/api/webhooks/stripe", json=mock_stripe_event_checkout_completed, headers={"stripe-signature": "test-bypass"})
        # ... assert organizations row updated
    
    def test_webhook_idempotent_on_duplicate_event_id():
        # send the same event twice, assert second call returns "already_processed" and does not double-update
    ```
11. Commit: `feat(billing): Stripe checkout session + webhook handler with idempotency`.

**End of Day 7 checklist:**
- [ ] `/api/billing/checkout-session` creates a working Stripe Checkout session
- [ ] `/api/billing/portal-session` lets existing customers manage subscription
- [ ] `/api/webhooks/stripe` verifies signature, handles all 5 target event types, is idempotent
- [ ] Confirmed raw body is available to the webhook route (no body-consuming middleware conflict)
- [ ] Tested end-to-end locally with Stripe CLI `stripe trigger`
- [ ] Unit tests for both checkout creation and webhook idempotency

---

### **Day 8 — Billing UI + Plan Enforcement Wiring + Invite Flow Fix**

**Morning block (`/settings/billing` page):**

1. Create `apps/web/src/app/settings/billing/page.tsx`. Fetch current org plan/status from `/api/me` or a dedicated `/api/billing/status` endpoint (create this small read endpoint first if it doesn't exist):
   ```python
   # apps/api/app/routers/billing.py — add:
   @router.get("/status")
   async def get_billing_status(user_and_org = Depends(get_current_user_and_org)):
       _, org = user_and_org
       return {
           "plan": org.get("plan", "free"),
           "plan_status": org.get("plan_status", "active"),
           "current_period_end": org.get("current_period_end"),
           "has_billing_account": bool(org.get("stripe_customer_id")),
       }
   ```
2. Build the page with: current plan name + status badge, a usage meter reusing the existing `QuotaMeter` component, three plan cards (Starter/Team/Business) each with a "Upgrade" or "Current Plan" button, and (if `has_billing_account` is true) a "Manage billing" button that calls `/api/billing/portal-session` and redirects to the returned URL.
3. Wire upgrade buttons: `onClick` → `POST /api/billing/checkout-session` with the selected plan → redirect `window.location.href = response.checkout_url`.
4. Handle the `?checkout=success` and `?checkout=canceled` query params on page load (set by the `success_url`/`cancel_url` in Day 7) with a toast notification.
5. **Fix every dead-end CTA identified in the original audit:** grep the frontend for `/settings` links from `QuotaMeter` and any `402 quota_exceeded` response handler. Update them to point at `/settings/billing` specifically, not the bare `/settings` page.
6. Manually test the full loop: free org → click upgrade on Team → redirected to real Stripe Checkout (test mode) → enter Stripe's test card `4242 4242 4242 4242` → complete → redirected back to `/settings/billing?checkout=success` → confirm plan badge now shows "Team" (this requires the webhook to have fired and updated the DB — there will be a 1-3 second delay; consider adding a brief polling check or just refetch-on-mount with a short delay/retry).

**Afternoon block (plan enforcement wiring + invite flow fix):**

7. Open wherever quota/seat-cap checks currently live (`invitations.py` seat cap pattern is the reference implementation per the original audit). Confirm document count and seat count limits read from `organizations.plan` (now populated by Stripe) rather than any hardcoded or missing value. Map plan → limits in one place:
   ```python
   PLAN_LIMITS = {
       "free": {"max_seats": 1, "max_documents": 10},
       "starter": {"max_seats": 5, "max_documents": 100},
       "team": {"max_seats": 20, "max_documents": 1000},
       "business": {"max_seats": None, "max_documents": None},  # unlimited
   }
   ```
8. **Invite flow fix for existing users** (P1 Bundle 4) — open `apps/web/src/app/accept-invite/page.tsx`. Current bug: calls `supabase.auth.signUp()` unconditionally even if the invited email already has a Supabase account. Fix:
   ```tsx
   async function handleAcceptInvite(email: string, password: string, inviteToken: string) {
     // First, check if a user with this email already exists by attempting sign-in
     // rather than blind signUp. Supabase doesn't expose a direct "does user exist" check
     // client-side, so the cleanest fix is a dedicated backend endpoint.
     const response = await fetch("/api/invitations/accept", {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ token: inviteToken, email, password }),
     });
     const result = await response.json();
     if (result.requires_login) {
       router.push(`/login?invite=${inviteToken}&email=${encodeURIComponent(email)}`);
       return;
     }
     // ... proceed to org, logged in
   }
   ```
9. Build the backend endpoint `POST /api/invitations/accept` that:
   - Looks up the invite token, validates it's unexpired and matches the email
   - Checks if a Supabase user with that email already exists (use `supabase_admin.auth.admin.list_users()` filtered by email, or attempt `get_user_by_email` if available in your SDK version)
   - If user exists: returns `{"requires_login": true}` and does NOT call `signUp`
   - If user doesn't exist: creates the user via `supabase_admin.auth.admin.create_user()`, adds them to `organization_members`, marks invite as accepted, returns `{"requires_login": false, "session": ...}`
10. **Fix the "Sign in instead" link path** — currently `/login?invite=TOKEN` does not complete invite acceptance. Update the login page: after successful login, if a `?invite=TOKEN` param is present, call `POST /api/invitations/accept` with the now-authenticated session to attach the user to the org, THEN redirect to `/chat`.
11. Write a test covering both paths: new-user invite acceptance, and existing-user invite acceptance via login.
12. Manually test: invite an email that already has an account on the platform (use a second test account you control), confirm it correctly routes to login + org attachment instead of erroring or duplicating.
13. Commit: `feat(billing): billing settings page, plan enforcement; fix(invites): existing-user invite acceptance flow`.

**End of Day 8 checklist:**
- [ ] `/settings/billing` shows real plan/usage and has working upgrade buttons
- [ ] Full Stripe test-mode checkout loop verified end-to-end
- [ ] All dead-end upgrade CTAs across the app point to the real billing page
- [ ] Plan limits centrally defined and read from Stripe-synced `organizations.plan`
- [ ] Invite flow correctly handles existing Supabase users (no more duplicate/error path)
- [ ] "Sign in instead" link completes invite acceptance after login

---

### **Day 9 — Document Upload Limits, Rate Limiting, Role Promotion UI**

**Morning block (document upload plan enforcement + rate limiting — P1 Bundle 2):**

1. Open `apps/api/app/routers/documents.py`. Find the upload endpoint(s). Before processing any upload, add a plan-aware count check mirroring the seat-cap pattern from `invitations.py`:
   ```python
   from app.core.plan_limits import PLAN_LIMITS

   @router.post("/upload/init")
   async def init_upload(..., user_and_org = Depends(get_current_user_and_org)):
       _, org = user_and_org
       limits = PLAN_LIMITS.get(org["plan"], PLAN_LIMITS["free"])
       max_docs = limits["max_documents"]
       if max_docs is not None:
           current_count = supabase_client.table("documents").select("id", count="exact").eq("org_id", org["id"]).execute().count
           if current_count >= max_docs:
               raise HTTPException(
                   status_code=403,
                   detail=f"Document limit reached for your plan ({max_docs}). Upgrade to add more.",
               )
       # ... proceed with upload init
   ```
2. Move `PLAN_LIMITS` into a shared `app/core/plan_limits.py` module (referenced from both Day 8's seat logic and this check) to avoid duplicate definitions drifting apart.
3. Write a test: seed an org at exactly its plan's document limit, attempt one more upload, confirm 403 with a clear message.

**Rate limiting — reuse the existing Redis-backed chat rate limiter pattern:**

4. Open the existing chat rate limiter implementation (wherever `enforce_chat_rate_limit`'s *non-deprecated* sibling lives, or the core rate-limit utility it was based on). Generalize it into a reusable decorator/dependency:
   ```python
   # app/core/rate_limit.py
   def make_rate_limiter(key_prefix: str, limit: int, window_seconds: int):
       async def _check(user_and_org = Depends(get_current_user_and_org)):
           user, _ = user_and_org
           redis_key = f"ratelimit:{key_prefix}:{user.id}"
           current = redis_client.incr(redis_key)
           if current == 1:
               redis_client.expire(redis_key, window_seconds)
           if current > limit:
               raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")
       return _check

   upload_init_limiter = make_rate_limiter("upload_init", limit=20, window_seconds=3600)
   oauth_callback_limiter = make_rate_limiter("oauth_callback", limit=10, window_seconds=3600)
   admin_aggregation_limiter = make_rate_limiter("admin_agg", limit=30, window_seconds=3600)
   ```
5. Apply to the upload endpoints:
   ```python
   @router.post("/upload/init", dependencies=[Depends(upload_init_limiter)])
   @router.post("/upload/complete", dependencies=[Depends(upload_init_limiter)])
   ```
6. Apply to OAuth callback endpoints (`/integrations/drive/callback`, `/slack/oauth`, `/gmail/callback`):
   ```python
   @router.get("/drive/callback", dependencies=[Depends(oauth_callback_limiter)])
   ```
   **Note the edge case:** OAuth callbacks are hit by the OAuth provider's redirect, not a logged-in API call necessarily carrying your own auth headers in the same way — confirm the rate limiter dependency can still resolve a user identity at this point in the flow (it should, since the user has an active session cookie when the redirect lands), or key the rate limit by IP address instead if user identity isn't reliably available at that exact point:
   ```python
   def make_ip_rate_limiter(key_prefix: str, limit: int, window_seconds: int):
       async def _check(request: Request):
           ip = request.client.host
           redis_key = f"ratelimit:{key_prefix}:{ip}"
           # ... same incr/expire/check logic
       return _check
   ```
7. Apply to admin aggregation endpoints (`/admin/analytics`, `/admin/coverage`, `/admin/health-scores`):
   ```python
   @router.get("/analytics", dependencies=[Depends(admin_aggregation_limiter)])
   ```
8. Write tests confirming the 21st upload-init request within an hour returns 429, and that the limit resets after the window (use `freezegun` or manual Redis TTL manipulation in tests to avoid actually waiting an hour).

**Afternoon block (admin analytics pagination — quick scalability fix bundled here since it touches the same endpoints):**

9. Open `GET /admin/analytics`. Find the unbounded `messages` query loading up to 50,000 rows. Add either a `LIMIT` with pagination, or — better, since it's aggregation — push the aggregation into a Postgres RPC function:
   ```sql
   CREATE OR REPLACE FUNCTION get_org_analytics(p_org_id UUID, p_start_date TIMESTAMPTZ, p_end_date TIMESTAMPTZ)
   RETURNS TABLE(date DATE, message_count BIGINT, unique_users BIGINT) AS $$
     SELECT date_trunc('day', created_at)::date, count(*), count(DISTINCT user_id)
     FROM messages
     WHERE org_id = p_org_id AND created_at BETWEEN p_start_date AND p_end_date
     GROUP BY 1
     ORDER BY 1;
   $$ LANGUAGE sql STABLE;
   ```
   Call this via `supabase_client.rpc("get_org_analytics", {...}).execute()` instead of pulling raw rows into Python.
10. Repeat the same RPC pattern for `/admin/coverage` and `/admin/health-scores` if they share the same unbounded-row-load problem.

**Member role promotion UI (P1 Bundle 4):**

11. Build backend endpoint `PATCH /api/organizations/members/{member_id}`:
    ```python
    @router.patch("/members/{member_id}")
    async def update_member_role(member_id: str, new_role: str, user_and_org = Depends(get_current_user_and_org)):
        _, org = user_and_org
        if org["role"] not in ("admin", "owner"):
            raise HTTPException(status_code=403, detail="Only admins can change member roles.")
        if new_role not in ("member", "admin"):
            raise HTTPException(status_code=400, detail="Invalid role.")
        # prevent demoting the last remaining owner/admin — check count before applying
        supabase_client.table("organization_members").update({"role": new_role}).eq("id", member_id).eq("org_id", org["id"]).execute()
        return {"status": "updated"}
    ```
12. On the frontend members list, replace the `MoreHorizontal` menu's single "Remove from workspace" option with a small dropdown including "Make admin" / "Make member" (whichever is not current) and "Remove from workspace".
13. Add a guard: if the action would remove the last admin/owner from an org, show a disabled state with a tooltip "An organization must have at least one admin."
14. Test: promote a member, confirm role updates and they immediately gain admin-only UI access (or on next page load/session refresh, depending on whether roles are cached client-side).
15. Commit: `feat(security): upload/OAuth/admin rate limiting, plan-based document limits; perf: analytics RPC pagination; feat(org): member role promotion`.

**End of Day 9 checklist:**
- [ ] Document upload blocked at plan limit with clear error
- [ ] Rate limiting live on upload init/complete, OAuth callbacks, admin aggregation endpoints
- [ ] Admin analytics/coverage/health-scores no longer load unbounded rows into Python
- [ ] Member role promotion endpoint + UI working, with last-admin guard

---

### **Day 10 — In-App Password Change, Session Expiry Redirect, drive_picker_token Fix**

**Morning block (in-app password change):**

1. Backend endpoint `PATCH /api/users/me/password`:
   ```python
   @router.patch("/me/password")
   async def change_password(payload: ChangePasswordRequest, user_and_org = Depends(get_current_user_and_org)):
       user, _ = user_and_org
       if len(payload.new_password) < 8:
           raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
       try:
           supabase_client.auth.update_user({"password": payload.new_password})
       except Exception as exc:
           logger.error(f"Password update failed for user {user.id}: {exc}", exc_info=True)
           raise HTTPException(status_code=400, detail="Could not update password. Please try again.")
       return {"status": "updated"}
   ```
   **Note:** Supabase's `update_user` for password change typically requires the *user's own* authenticated client (not the admin client) since it operates on the current session — confirm you're calling this with the request-scoped Supabase client carrying the user's JWT, not the service-role admin client.
2. Add a "Change Password" card to the existing profile/settings page (wherever account deletion currently lives — put it nearby for discoverability):
   ```tsx
   function ChangePasswordCard() {
     const [currentPassword, setCurrentPassword] = useState(""); // optional: re-auth check, see note below
     const [newPassword, setNewPassword] = useState("");
     const [confirmPassword, setConfirmPassword] = useState("");
     // ... form + submit handler calling PATCH /api/users/me/password
   }
   ```
3. **Security consideration:** decide whether to require re-entering the current password before allowing a change (defends against a session-hijack scenario where an attacker with a stolen session token could lock the real user out by changing their password). Supabase doesn't require current-password verification by default for `update_user`. For a 14-day timeline, document this as an accepted risk and note it in Technical Debt rather than building a full re-auth flow — but DO add a confirmation email: check if Supabase sends a "password changed" notification email by default (Authentication → Email Templates), and enable it if not already on, so the legitimate user is alerted if their password is changed without their action.
4. Test: change password while logged in, log out, confirm old password fails and new password succeeds.

**Afternoon block (session expiry redirect):**

5. Find the SWR fetcher configuration (likely a shared `fetcher.ts` or similar used across `useSWR` calls for `/api/me` and others).
6. Add a global response interceptor:
   ```typescript
   export const fetcher = async (url: string) => {
     const res = await fetch(url, { credentials: "include" });
     if (res.status === 401) {
       // session expired mid-use — redirect to login rather than showing a stuck error state
       window.location.href = `/login?redirectedFrom=${encodeURIComponent(window.location.pathname)}`;
       throw new Error("Session expired");
     }
     if (!res.ok) {
       throw new Error(`Request failed: ${res.status}`);
     }
     return res.json();
   };
   ```
7. **Edge case:** make sure this doesn't create a redirect loop if `/login` itself ever calls something through this fetcher and gets a 401 (it shouldn't, since `/login` is unauthenticated by nature, but double check no `useSWR` call on the login page itself uses this fetcher against a protected endpoint).
8. Test: log in, open devtools, manually expire/delete the session cookie, trigger any SWR-fetched call (e.g., navigate to a page that refetches `/api/me`), confirm immediate redirect to `/login` instead of a hung "Failed to load profile (401)" state.

**drive_picker_token fix:**

9. Open `routers/integrations.py`, find `GET /integrations/drive/picker-token`. Current behavior returns the raw Google OAuth access token directly to the browser. Replace with a server-side proxy approach:
   - Option A (faster for 14-day timeline): scope-narrow the token before returning — request a short-lived, narrowly-scoped token specifically for the Picker API (Google supports requesting a token with only `drive.file` scope rather than full Drive access) rather than reusing the broad stored token.
   - Option B (more correct, more work): never return the token to the browser at all; instead, proxy all Picker API interactions through your backend.
   - **Given the timeline, implement Option A**: 
   ```python
   @router.get("/drive/picker-token")
   async def get_picker_token(user_and_org = Depends(get_current_user_and_org)):
       # Request a narrowly-scoped, short-lived token specifically for Picker use,
       # rather than returning the broadly-scoped stored integration token.
       narrow_token = await get_narrow_scope_drive_token(org_id=..., scopes=["https://www.googleapis.com/auth/drive.file"])
       return {"token": narrow_token, "expires_in": 3600}
   ```
10. Document in code comments that Option B (full server-side proxy) is the more correct long-term fix and is tracked in the backlog below.
11. Test the Drive picker still functions end-to-end with the narrower token (file selection, not full drive browsing, should still work for the picker's actual use case — confirm the picker doesn't need broader scope for its UI to render, only for the actual file access after selection).
12. Commit: `feat(account): in-app password change; fix(auth): session expiry redirect; security: narrow-scope drive picker token`.

**End of Day 10 checklist:**
- [ ] Password change works from within the app, confirmation email enabled
- [ ] 401 responses from SWR globally redirect to `/login` instead of hanging
- [ ] Drive picker token narrowed in scope, full proxy approach documented as backlog

---

### **Day 11 — GDPR Data Export + Privacy/Terms Page Scaffolding (P1 Bundle 3, Part 1)**

**Morning block (GDPR export endpoint):**

1. Create `GET /api/users/me/export` in a new or existing `users.py` router:
   ```python
   import json
   import zipfile
   import io

   @router.get("/me/export")
   async def export_user_data(user_and_org = Depends(get_current_user_and_org)):
       user, org = user_and_org

       conversations = supabase_client.table("conversations").select("*").eq("user_id", user.id).execute().data
       conversation_ids = [c["id"] for c in conversations]
       messages = (
           supabase_client.table("messages").select("*").in_("conversation_id", conversation_ids).execute().data
           if conversation_ids else []
       )
       query_logs = supabase_client.table("query_logs").select("*").eq("user_id", user.id).execute().data
       documents = supabase_client.table("documents").select("id, filename, created_at, file_size").eq("uploaded_by", user.id).execute().data
       profile = {"id": user.id, "email": user.email, "created_at": user.created_at}

       buffer = io.BytesIO()
       with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
           zf.writestr("profile.json", json.dumps(profile, default=str, indent=2))
           zf.writestr("conversations.json", json.dumps(conversations, default=str, indent=2))
           zf.writestr("messages.json", json.dumps(messages, default=str, indent=2))
           zf.writestr("query_logs.json", json.dumps(query_logs, default=str, indent=2))
           zf.writestr("documents_metadata.json", json.dumps(documents, default=str, indent=2))
           zf.writestr("README.txt", "This archive contains your personal data as stored by NirnayaIQ, exported under GDPR Article 20 (Right to Data Portability). Document metadata is included; full document file contents are not included in this export — contact support if you require the underlying files.")

       buffer.seek(0)
       return StreamingResponse(
           buffer,
           media_type="application/zip",
           headers={"Content-Disposition": f"attachment; filename=nirnayaiq-export-{user.id}.zip"},
       )
   ```
2. **Important scope decision:** the spec mentions "uploaded document names" specifically, not full file contents — this implementation includes document *metadata* (filename, size, date) rather than the actual file bytes, since pulling every uploaded file into a ZIP could be very large and slow for the endpoint to generate synchronously. If you want full file inclusion, this needs to become an async job (Inngest function that generates the export and emails a download link) rather than a synchronous request — flag this tradeoff and confirm metadata-only is acceptable for launch, with full-file export as a fast-follow.
3. Add a basic rate limit to this endpoint too (reuse `make_rate_limiter`) — exports are expensive queries and shouldn't be spammable: `dependencies=[Depends(make_rate_limiter("data_export", limit=3, window_seconds=86400))]`.
4. Write a test confirming the exported ZIP contains the expected files and that a user cannot trigger another user's export (the endpoint should only ever use `user.id` from the authenticated session, never accept a target user ID as a parameter — confirm no such parameter exists).
5. Add a "Download my data" button on the account/settings page that calls this endpoint and triggers a browser download.

**Afternoon block (Privacy/Terms page scaffolding + cookie consent):**

6. Create `apps/web/src/app/privacy/page.tsx` and `apps/web/src/app/terms/page.tsx` as static pages with a clear placeholder structure:
   ```tsx
   export default function PrivacyPolicyPage() {
     return (
       <div className="max-w-3xl mx-auto px-4 py-12 prose">
         <h1>Privacy Policy</h1>
         <p className="text-sm text-muted-foreground">Last updated: [DATE]</p>
         {/* 
           PLACEHOLDER: Replace this content with final legal copy.
           Recommended sections: Data we collect, How we use it, Third-party processors
           (Supabase, Stripe, Gemini/Google, Resend), Data retention, User rights (GDPR/CCPA),
           Contact information, Cookie policy.
           Consider a service like Termly, Iubenda, or direct legal counsel for final text.
         */}
         <p>[Privacy policy content pending final legal review.]</p>
       </div>
     );
   }
   ```
   Build `terms/page.tsx` identically in structure.
7. Add footer links to `/privacy` and `/terms` site-wide (likely in a shared layout footer component).
8. Build a simple cookie consent banner (since you likely use analytics/tracking cookies — confirm what you actually set: Supabase auth cookies are functionally necessary and don't require consent banners under most frameworks, but if you have any analytics tracking, e.g. PostHog/Mixpanel/GA, consent is required for EU visitors):
   ```tsx
   "use client";
   import { useState, useEffect } from "react";

   export function CookieConsentBanner() {
     const [visible, setVisible] = useState(false);
     useEffect(() => {
       if (!localStorage.getItem("cookie_consent")) setVisible(true);
     }, []);
     function accept() {
       localStorage.setItem("cookie_consent", "accepted");
       setVisible(false);
     }
     if (!visible) return null;
     return (
       <div className="fixed bottom-0 inset-x-0 bg-background border-t p-4 flex items-center justify-between gap-4 z-50">
         <p className="text-sm">We use cookies to keep you signed in and improve your experience. See our <a href="/privacy" className="underline">Privacy Policy</a>.</p>
         <button onClick={accept} className="bg-primary text-white px-4 py-2 rounded shrink-0">Accept</button>
       </div>
     );
   }
   ```
   Mount this in the root layout.
9. **Confirm with yourself before Day 12:** do you have, or can you get within this sprint, actual legal text for these two pages? If not, the pages and routes will exist and be functionally complete (correct links, correct structure) but will display clearly-marked placeholder text. This is sufficient to not 404, but is **not** sufficient for actual legal compliance at commercial launch — flag this explicitly as an open item in the final verdict.
10. Commit: `feat(gdpr): data export endpoint; feat(legal): privacy/terms page scaffolding + cookie consent banner`.

**End of Day 11 checklist:**
- [ ] `/api/users/me/export` returns a working ZIP with conversations, messages, query logs, document metadata
- [ ] Export endpoint rate-limited and scoped strictly to the requesting user
- [ ] "Download my data" button live in settings
- [ ] `/privacy` and `/terms` routes exist with placeholder content clearly marked for legal review
- [ ] Cookie consent banner live
- [ ] **Open item flagged:** final legal copy still needed before this satisfies actual compliance requirements

---

### **Day 12 — Buffer / Hardening Day for Billing + GDPR + Full Regression Pass**

Billing is the highest-risk surface in this entire roadmap (money, third-party webhooks, race conditions between checkout completion and webhook delivery). Use this day to harden rather than add new scope.

1. **Webhook race condition check:** what happens if a user completes Checkout and is redirected to `success_url` before the webhook has actually been processed? The `/settings/billing` page might briefly show the old (free) plan. Add a short client-side retry: on `?checkout=success`, poll `/api/billing/status` every 2 seconds for up to 15 seconds, or simply show "Processing your upgrade... this may take a few seconds" with a manual refresh button.
2. **Webhook failure recovery:** in Stripe Dashboard → Webhooks → your endpoint, review the "recent events" panel. Manually fail a test delivery (or use `stripe trigger` with the API down temporarily) and confirm Stripe's automatic retry behavior works, and that your idempotency check correctly handles a delayed retry landing after you've already manually fixed the issue.
3. **Test every subscription lifecycle transition manually using Stripe test clock or `stripe trigger`:**
   - [ ] New subscription → `organizations.plan` updates correctly
   - [ ] Upgrade from Starter to Team → plan updates, no duplicate customer created
   - [ ] Downgrade → plan updates, document/seat limits re-enforced (test: an org with 150 docs downgrades to Starter's 100-doc limit — confirm existing docs are NOT deleted, but new uploads are blocked until under the limit)
   - [ ] Payment failure → `plan_status` becomes `past_due`, confirm app surfaces this status visibly somewhere (billing page badge at minimum)
   - [ ] Subscription cancellation → `plan` reverts to `free`, enforcement kicks in
4. **Test the billing portal flow:** click "Manage billing," confirm redirect to Stripe's hosted portal, cancel a test subscription from within the portal, confirm the resulting webhook correctly syncs back.
5. **GDPR export edge cases:** test export for a brand new user with zero conversations (should return a valid but mostly-empty ZIP, not error). Test export for a user with a large number of conversations (confirm reasonable response time, no timeout — if this is slow, note as backlog item to convert to async Inngest job).
6. **Full regression pass across everything built so far (Days 1-11):** run through the Day 5 regression checklist again, plus:
   - [ ] Billing checkout loop end-to-end with test card
   - [ ] Invite flow for both new and existing users
   - [ ] Role promotion + last-admin guard
   - [ ] Rate limits trigger correctly (don't wait an hour — verify via Redis key inspection that the counter increments and TTL is set correctly, that's sufficient without needing to actually exhaust the limit each time)
   - [ ] GDPR export downloads and contains correct data, scoped to the right user
   - [ ] Privacy/Terms pages load, footer links work, cookie banner appears once and persists dismissal
7. Fix anything broken found during this pass. Do not start Day 13 scope until this list is clean.
8. Tag a checkpoint: `git tag v-billing-gdpr-complete`.

**End of Day 12 checklist:**
- [ ] All subscription lifecycle transitions manually verified against real Stripe test events
- [ ] Webhook race condition on checkout redirect addressed with polling/messaging
- [ ] Full regression pass clean across all Days 1-11 work
- [ ] Git checkpoint tagged

---

### **Day 13 — Final P1 Cleanup, Payment Failure Notification, Pre-Launch Audit**

**Morning block:**

1. **Payment failure notification** (flagged as a TODO on Day 7) — wire the `invoice.payment_failed` webhook branch to actually notify the org admin, not just set `plan_status`. Use whatever notification mechanism already exists (the `notifications` table mentioned in the original audit, or email via Resend if already integrated):
   ```python
   elif event_type == "invoice.payment_failed":
       # ... existing plan_status update ...
       admins = supabase_admin.table("organization_members").select("user_id").eq("org_id", org_id).eq("role", "admin").execute().data
       for admin in admins:
           supabase_admin.table("notifications").insert({
               "user_id": admin["user_id"],
               "org_id": org_id,
               "type": "payment_failed",
               "message": "Your subscription payment failed. Please update your billing details to avoid service interruption.",
           }).execute()
   ```
2. Double-check every item from the **"Final Verdict — Prioritized launch checklist"** in the original audit is genuinely done:
   - [ ] Messages RLS SELECT policy → user-scoped
   - [ ] SSRF private IP validation on webhook + agent callback delivery
   - [ ] Forgot-password + reset-password flow
   - [ ] Next.js middleware.ts edge route protection
   - [ ] CI/CD GitHub Actions with test + typecheck gates
   - [ ] Stripe billing integration + `/settings/billing` page
   - [ ] Inngest signing key enforced at startup
   - [ ] Exception internals no longer leak into API responses
3. Re-verify all P1 Bundle items are complete:
   - [ ] Bundle 1: Inngest signing, exception leaks, CORS, API key timing — Day 1
   - [ ] Bundle 2: upload limits + rate limiting — Day 9
   - [ ] Bundle 3: GDPR export + privacy/terms scaffolding — Day 11
   - [ ] Bundle 4: invite flow, role promotion, password change — Days 8, 9, 10

**Afternoon block (pre-launch security/ops audit):**

4. Run a final secrets audit: `grep -rn "sk_test\|sk_live\|whsec_" --include="*.py" --include="*.ts" --include="*.tsx" apps/` to confirm no Stripe secret accidentally got hardcoded anywhere instead of read from environment.
5. Confirm `.env.example` (or equivalent) is updated with every new environment variable introduced this sprint: `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_STARTER_MONTHLY`, `STRIPE_PRICE_TEAM_MONTHLY`, `STRIPE_PRICE_BUSINESS_MONTHLY`.
6. Confirm Railway production environment variables are set for all of the above (test mode keys for now — swapping to live mode is Day 14).
7. Review Stripe Dashboard → Settings → Customer Portal configuration: confirm cancellation, plan switching, and payment method update are enabled (these are configurable toggles in Stripe's portal settings, easy to miss).
8. Run the full CI suite one more time locally (`pytest`, `tsc --noEmit`, `pnpm build`) to confirm a clean baseline before declaring readiness.
9. Write/update `LAUNCH_CHECKLIST.md` consolidating everything verified across Days 1-13.
10. Commit: `feat(billing): payment failure notifications; chore: pre-launch audit and checklist`.

**End of Day 13 checklist:**
- [ ] Payment failure triggers an actual admin-facing notification
- [ ] Every item from the original "Final Verdict" checklist independently re-verified
- [ ] No secrets hardcoded anywhere in the codebase
- [ ] `.env.example` and Railway env vars fully in sync
- [ ] Stripe Customer Portal settings configured correctly
- [ ] `LAUNCH_CHECKLIST.md` written

---

### **Day 14 — Stripe Live Mode Cutover + Final Production Smoke Test + Launch**

1. **Stripe Live Mode switch:** in Stripe Dashboard, toggle from Test to Live mode. Recreate the same 3 Products/Prices in Live mode (Stripe does not carry test-mode objects over — this is a deliberate separation). Record the new Live Price IDs.
2. Create a **second** webhook endpoint in Stripe Live mode pointing at your production URL, select the same 5 event types as Day 6, copy the new Live signing secret.
3. Update Railway production environment variables with Live-mode values: `STRIPE_SECRET_KEY=sk_live_xxx`, `STRIPE_PUBLISHABLE_KEY=pk_live_xxx`, `STRIPE_WEBHOOK_SECRET=whsec_xxx` (the live one), and the three new Live Price IDs.
4. Redeploy (or confirm Railway picks up the new env vars on next deploy — depending on Railway's behavior, you may need to trigger a manual redeploy for env var changes to take effect).
5. **Critical: do a real $-amount test transaction** with an actual card (your own), on the cheapest plan, to confirm Live mode billing genuinely works end-to-end — Test mode passing does not guarantee Live mode is configured identically (a surprisingly common gotcha is Live mode webhook secret mismatch, or a Live Price ID typo). Immediately refund this transaction from the Stripe Dashboard after confirming it worked.
6. Run the full Day 12 regression checklist one final time against production with Live billing active (everything except repeatedly running real-money transactions — use the single verified transaction from step 5 as sufficient proof, then return to careful read-only checks for the rest).
7. Confirm DNS, SSL, and any custom domain settings are correctly pointed if not already done.
8. Confirm Railway's deploy health check (`/health`) is green in production right now.
9. Do a final pass through `LAUNCH_CHECKLIST.md` from Day 13, checking off each item against the live production environment specifically (not staging, not test mode).
10. Tag the release: `git tag v1.0.0-launch`.
11. **Launch.** Flip whatever "coming soon"/waitlist gating exists (if any) to allow real signups, or announce per your go-to-market plan.
12. Immediately after launch, monitor for the first few hours: watch Railway logs, watch Stripe Dashboard for incoming events, watch `/health` status, and keep the Stripe webhook "recent events" panel open to catch any delivery failures in real time.

**End of Day 14 checklist:**
- [ ] Stripe Live mode fully configured with correct Price IDs and webhook secret
- [ ] One real test transaction completed and refunded successfully
- [ ] Full regression pass clean in production with Live billing
- [ ] `v1.0.0-launch` tagged
- [ ] **LAUNCH**
- [ ] Active monitoring for first few hours post-launch

---

## Post-Launch Backlog (Explicitly Deferred From This 14-Day Sprint)

These were named in your original audit but intentionally excluded from the 14-day plan above to keep scope achievable. Tackle in roughly this order once stable post-launch:

1. **JWT/session caching** — 60s LRU cache on `verify_jwt` keyed by token hash, to reduce per-request Supabase auth network calls. (Deferred per your confirmation.)
2. **Connection pooling** — PgBouncer or Supabase's built-in pooler for `get_user_client()`. (Deferred per your confirmation.)
3. **Full Drive picker token proxy (Option B)** — replace the narrow-scope-token mitigation from Day 10 with a true server-side proxy that never exposes any OAuth token to the browser.
4. **DNS-rebinding-proof SSRF protection** — pin resolved IP for the actual outbound request rather than re-resolving, to close the narrow rebinding window noted on Day 2.
5. **Final legal copy for Privacy Policy and Terms of Service** — replace Day 11 placeholders with real, lawyer-reviewed or professionally-templated text. This is the single most important post-launch item for actual (not just structural) compliance.
6. **Async GDPR export for large accounts** — convert the Day 11 synchronous export into an Inngest background job + email-the-download-link pattern, and include full document file contents, not just metadata.
7. **Indirect prompt injection defense** (XML chunk delimiters), **output PII scanning**, **Unicode NFKC normalization in moderation** — all from the original P2 security list.
8. **Notification preferences UI, onboarding activation checklist, NPS survey** — retention-focused P1/P2 items not in this sprint's scope.
9. **Org ownership transfer, leave-workspace option, bulk conversation delete** — org-management completeness items.
10. **Per-org AI token budget tracking and alerts** — cost-control item, becomes more urgent as the Business (unlimited-queries) plan gets real usage.
11. **Application-level OAuth token encryption** (`pgcrypto`) for the `integrations` table.
12. **Webhook delivery log UI, OAuth token refresh failure notifications, agent auto-post human review gate** — operational visibility items.
13. **Re-auth-before-password-change** — close the gap noted on Day 10 where password change doesn't require re-entering the current password.
14. **All remaining P3 items** from the original audit (extension CORS pinning, archived chunk purge cron, API key scoping/rotation, empty states, structured alerting via PagerDuty/Sentry, etc.)

---

## Risks to This Timeline

Flagging honestly, not to discourage — these are the most likely sources of slippage:

- **Stripe webhook debugging (Day 7)** is historically the single most time-consuming piece of any billing integration, even for experienced teams. The buffer days (5 and 12) exist primarily to absorb this risk. If Day 7 runs significantly over, pull time from Day 12's hardening rather than from Day 1-4 security work, which is non-negotiable.
- **Existing-user invite flow (Day 8)** depends on your Supabase SDK version exposing a usable "check if user exists by email" capability via the admin API — if your SDK version makes this awkward, the fallback (attempt `create_user` and catch the "already exists" error) works but is slightly less clean; budget extra time if this happens.
- **Admin role check in middleware (Day 3)** depends on where role data actually lives in your schema (`app_metadata` vs. a join table) — if it's the latter, middleware needs a DB round-trip, which is a real (if small) latency cost on every admin page load that's worth measuring before launch.