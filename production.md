# NirnayaIQ — Production Readiness Roadmap

---

## Executive Summary

**Current Production Readiness Score: 48 / 100**

**Launch Risk: HIGH**

**Estimated Weeks to Production-Ready: 5–6 weeks**

The core product is genuinely impressive for a solo engineer. The AI retrieval pipeline (hybrid RRF search, tool-use loop, streaming, agents), the 9 integrations, the compliance workflows, and the admin tooling are all built and functional. The codebase has shipped through v5 plus an agent sprint — this is not an MVP skeleton.

But there are five issues that make it unshippable today:

1. **There is no billing system.** Zero Stripe. The product cannot charge anyone. The upgrade CTAs in the UI go to a page that doesn't exist. This alone blocks commercial launch.
2. **Forgotten passwords permanently lock users out.** No forgot-password page, no reset flow, the SDK method is installed but never called.
3. **All team members can read each other's private chats.** The `messages` RLS SELECT policy is scoped to `org_id`, not `user_id`. Every authenticated employee in an org sees every other employee's conversations with the AI. This is a privacy violation that will kill trust the moment it's discovered.
4. **SSRF vulnerabilities.** Outbound webhook delivery and agent API callbacks accept arbitrary URLs with no private IP block. An org admin can point a webhook at `169.254.169.254` and exfiltrate Railway instance metadata.
5. **No CI/CD.** Every push to `main` auto-deploys to production with zero automated gating. A typo in `main.py` breaks production with no warning.

Fix these five things first. Everything else is secondary.

---

## P0 — Launch Blockers

| Feature | Why It Matters | Estimated Effort |
|---------|---------------|-----------------|
| **Stripe billing integration** | No payment = no revenue. `QuotaMeter` upgrade CTAs link to `/settings` which has no billing section. Help docs describe a billing page that doesn't exist. Business model is entirely un-monetized. | 2 weeks |
| **Forgot password / reset password flow** | `supabase.auth.resetPasswordForEmail()` is never called. No `/forgot-password` page. No link from login. Any email/password user who forgets their password is permanently locked out with no self-serve recovery. | 1 day |
| **Fix messages RLS policy** | `SELECT USING (org_id = auth_org_id())` lets any team member query every message in the org — including other users' private AI conversations. Change to `USING (org_id = auth_org_id() AND conversation_id IN (SELECT id FROM conversations WHERE user_id = auth.uid()))`. | 2 hours |
| **SSRF: block private IPs on outbound HTTP** | `inngest/webhook_functions.py` and `inngest/api_trigger_functions.py` POST to user-supplied URLs with no IP range check. An org admin can reach `169.254.169.254` (AWS metadata), internal Railway services, or the Supabase management API. Add a DNS-resolving private-IP validator before any `httpx` outbound call. | 4 hours |
| **CI/CD pipeline** | Zero GitHub Actions. Deploys happen via direct `git push main` with no test run, no type check, no build smoke test. One bad push breaks production silently. Add: `pytest -m "not integration"`, `tsc --noEmit`, `pnpm build` as PR gates. | 1 day |
| **Next.js route protection middleware** | `apps/web/middleware.ts` does not exist. Unauthenticated users hitting `/chat` directly see a brief flash before the server-component redirect fires. Every protected route is unguarded at the edge. | 4 hours |

---

## P1 — Must-Haves Before Paying Customers

| Feature | Why It Matters | Estimated Effort |
|---------|---------------|-----------------|
| **Enforce Inngest signing key in production** | `apps/api/app/inngest/client.py` wraps the signing key in `if settings.inngest_signing_key:` — if the env var is unset, `/api/inngest` accepts unsigned requests from anyone on the internet. Anyone can trigger document ingestion, agent runs, or compliance workflows. Add a startup assertion: `if settings.environment == "production" and not settings.inngest_signing_key: raise RuntimeError(...)`. | 1 hour |
| **Enforce document upload limits by plan** | Starter = 100 docs, Team = 1,000 per pricing. `routers/documents.py` has no plan check on upload. Any plan can upload unlimited documents. Add a document count check identical to the seat cap check in `invitations.py`. | 4 hours |
| **Rate limit document upload endpoints** | `POST /documents/upload/init` and `POST /documents/upload/complete` have no rate limiting. An attacker can queue thousands of Inngest ingestion jobs in seconds, saturating the embedding API quota. Add a per-user rate limit (e.g., 20 inits/hour) consistent with the chat rate limiter. | 4 hours |
| **Rate limit OAuth and admin endpoints** | OAuth callback endpoints (`/integrations/drive/callback`, `/slack/oauth`, `/gmail/callback`) and admin aggregation endpoints (`/admin/analytics`, `/admin/coverage`, `/admin/health-scores`) have no rate limiting. The analytics endpoint loads up to 50,000 `messages` rows in-process with no pagination — sustained polling will OOM the Railway process. | 1 day |
| **Fix exception internals leaking into API responses** | Five confirmed sites: `documents.py` lines 187 and 293 (`detail=f"...{exc}"`), `public_api.py` lines 81, 296, 498 (`detail=str(exc)`). These return Supabase SDK internals (bucket names, paths) and ValueError messages directly to clients. Replace with fixed strings; log the exception server-side. | 2 hours |
| **Tighten CORS configuration** | `allow_methods=["*"]` and `allow_headers=["*"]` in `main.py`. Restrict to the exact set used: methods `["GET","POST","PUT","DELETE","PATCH","OPTIONS"]`, headers `["Content-Type","Authorization","X-Request-ID"]`. | 30 minutes |
| **API key enumeration timing attack** | `verify_key()` raises `ValueError("API key not found")` vs `ValueError("API key revoked")` — these different messages reach the 401 response body, telling an attacker whether a guessed key exists. Both cases must return the identical message `"Invalid API key."`. | 30 minutes |
| **drive_picker_token returns raw OAuth token** | `GET /integrations/drive/picker-token` returns a live Google OAuth access token to the browser. If an XSS vulnerability ever exists in the frontend, this token exfiltrates Drive access. Return a short-lived signed proxy ticket or scope-narrow via server-side intermediary instead. | 1 day |
| **Fix invite flow for existing users** | `accept-invite/page.tsx` calls `supabase.auth.signUp()` unconditionally. An existing Supabase user who receives an invite cannot join the org — the form either errors or creates a duplicate. The "Sign in instead" link (`/login?invite=TOKEN`) does not complete invite acceptance. | 1 day |
| **Session expiry redirect** | When `GET /api/me` returns 401 (session expired mid-session), the app shows "Failed to load profile (401)" and hangs — no redirect to `/login`. Add a global 401 interceptor in the SWR fetcher config. | 4 hours |
| **Member role promotion UI** | There is no way to promote or demote an existing team member. The `MoreHorizontal` menu for members shows only "Remove from workspace." An accidentally mis-invited member requires removal + re-invite. Add a `PATCH /api/organizations/members/:id` endpoint and a "Change role" dropdown. | 1 day |
| **In-app password change** | Users can delete their account but cannot change their password from within the app. No `PATCH /users/me/password` endpoint. Supabase `auth.update_user({ password })` supports this. Add endpoint + profile card UI. | 4 hours |
| **Startup validation for critical env vars** | `gemini_api_key`, `oauth_state_secret`, and `inngest_signing_key` default to empty strings — the app boots silently but then fails at first LLM call, OAuth flow, or Inngest event. Add a `validate_production_config()` check in `lifespan` that raises `RuntimeError` when `ENVIRONMENT=production` and any of these are empty. | 2 hours |
| **Deep health check** | `GET /health` returns 200 as long as the process is alive. It does not probe Supabase connectivity, Redis, or LLM reachability. Railway uses this endpoint to gate deployments — a broken-DB deploy goes undetected. Add dependency probes with proper 503 on failure. | 4 hours |
| **Confirm query_logs RLS policy** | `query_logs` stores user query text and response metadata. Verify that an RLS SELECT policy exists that restricts reads to the querying user's own rows. If missing, the anon key can read all query text for all org members. | 1 hour |
| **GDPR data export endpoint** | No `GET /users/me/export` endpoint exists. GDPR Article 20 (right to data portability) requires this for any service with EU users. Return a ZIP/JSON bundle of conversations, messages, query history, and uploaded document names. | 2 days |
| **Notification preferences UI** | Users cannot control which notification types trigger in-app or email alerts. The `notifications` table supports various `type` values but there is no preference management UI or table. Without this, users have no way to reduce notification noise. | 2 days |
| **Onboarding activation flow** | The `EnrichmentModal` collects org metadata but there is no guided flow to upload a first document, complete a first chat, or invite a teammate. After dismissing the modal, new users land on an empty chat screen with no structured activation path. Add a getting-started checklist with tracked completion milestones. | 3 days |

---

## P2 — Post-Launch Priorities

| Feature | Why It Matters | Estimated Effort |
|---------|---------------|-----------------|
| **Indirect prompt injection defense** | Documents can contain `"Ignore previous instructions and..."` and the LLM will process them as legitimate context. Wrap retrieved chunks in XML delimiters (`<doc_chunk source="...">`) and instruct the system prompt to treat tag contents as untrusted. | 2 days |
| **Output PII scanning** | If the knowledge base contains HR records or customer data, the LLM reproduces it verbatim in chat responses. Add a lightweight PII regex pass (SSN, credit card, email patterns) on generated text before delivery. | 2 days |
| **Unicode normalization in moderation** | Moderation patterns lowercase input but do not apply NFKC normalization. Cyrillic/Greek homoglyphs and Unicode lookalikes bypass the injection block regex. One line fix: `unicodedata.normalize("NFKC", text)` at the top of `moderate_input()`. | 1 hour |
| **Webhook delivery log UI** | Failed webhook deliveries are retried by Inngest but there is no persistent UI showing delivery attempts, HTTP status codes, or response bodies. Admins cannot debug why a downstream system is not receiving events. | 1 day |
| **OAuth token refresh failure notifications** | When an Inngest poll job fails to refresh an expired OAuth token, `last_error` is set on the `integrations` row but no notification is sent. Users see stale integration data with no explanation. Add a notification trigger on `last_error` state change. | 4 hours |
| **Agent auto-post human review gate** | `MeetingNotesAgent` and `SupportResponseAgent` can auto-post results to Slack without human review. The approval workflow exists for document drafts but is not wired to agent outputs. Add an optional "require approval before posting" flag per agent type. | 2 days |
| **Negative feedback text capture** | Thumbs-down sends a `negative` signal but no text. Clicking thumbs-down should show a short text field ("What went wrong?") to capture actionable feedback. Currently the only negative signal is a binary flag. | 4 hours |
| **NPS survey** | No periodic satisfaction survey. Add a triggered NPS prompt (e.g., after 5th chat response or 14 days after first use) via the notifications system. | 1 day |
| **Route-level loading.tsx for remaining pages** | `history`, `notifications`, `insights`, `activity`, `approvals`, `archive`, and all admin pages lack route-level `loading.tsx` files. They rely on in-component `isLoading` checks, causing a blank content area flash on navigation. | 4 hours |
| **Google OAuth on signup page** | Login page has Google OAuth; signup page does not. Users who want to sign up via Google have no path — they must use email/password and then potentially face email confirmation issues. | 2 hours |
| **OAuth callback error handling** | `/auth/callback/route.ts` calls `exchangeCodeForSession` without error handling. An expired or invalid OAuth code silently redirects to `/chat` in a logged-out state. | 2 hours |
| **Leave workspace option for members** | Non-admin members can only delete their account entirely — there is no "Leave workspace" action. A member who wants to leave an org without deleting their Supabase account has no path. | 4 hours |
| **Org ownership transfer** | No `POST /admin/org/transfer-ownership` endpoint. If the sole admin deletes their account, the entire workspace is deleted. No way to promote another member to owner before leaving. | 1 day |
| **Bulk conversation delete** | No `DELETE /conversations/bulk` endpoint. Individual deletion and archiving exist, but admins cannot clean up old org data in bulk. | 4 hours |
| **AI cost controls: per-org AI budget** | Monthly query quotas are enforced, but there is no per-org token spend tracking or budget alert. On the Business plan (unlimited queries), a single power user can exhaust Gemini API credits. Add token count aggregation in `query_logs` and an alert threshold config. | 2 days |
| **Document count enforcement on upload** | Seat caps exist for invitations but document count limits (100 for Starter, 1,000 for Team) are tracked in pricing docs but not enforced in `documents.py`. | 4 hours |
| **Dockerfile: run as non-root user** | The Railway Docker container runs as `root`. Add `RUN useradd -m appuser` and `USER appuser` before `CMD`. | 30 minutes |
| **Supabase plan upgrade for backups** | On the free Supabase tier there are no automated backups. Upgrade to Supabase Pro for daily snapshots + point-in-time recovery, or add a scheduled `pg_dump` script. | N/A (cost decision) |
| **Connection pooling and JWT caching** | `get_user_client()` creates a new Supabase client per request. `verify_jwt` makes a network call to Supabase auth on every authenticated request with no in-process token cache. At 100+ concurrent users this will hit Supabase auth rate limits. Add a short-lived (60s) LRU cache keyed on token hash. | 2 days |
| **Admin analytics pagination** | `GET /admin/analytics` loads up to 50,000 `messages` rows into memory before aggregating. Add a `LIMIT` clause or push aggregation into a Postgres function. | 1 day |
| **Application-level OAuth token encryption** | OAuth access tokens and refresh tokens are stored in plaintext in `integrations` table. Supabase at-rest AES-256 covers disk encryption, but tokens are visible to anyone with DB console access. Use `pgcrypto pgp_sym_encrypt` for application-level encryption. | 2 days |
| **Webhook test/ping endpoint** | Admins configure webhook URLs but cannot fire a test event to verify delivery. Add `POST /webhooks/{id}/ping` that sends a synthetic `{event: "test"}` payload. | 4 hours |
| **Public API usage endpoint** | API key holders have no way to check current usage. They hit 429s with no warning. Add `GET /v1/usage` returning `{monthly_used, monthly_limit, reset_at}`. | 4 hours |
| **Privacy policy and Terms of Service pages** | No `/privacy` or `/terms` routes exist. No cookie consent banner. Required before any commercial launch in the EU or for any App Store submission. | 1 day (legal copy) |

---

## P3 — Nice-To-Haves

| Feature | Why It Matters | Estimated Effort |
|---------|---------------|-----------------|
| **Pin Chrome extension CORS to fixed extension ID** | `allow_origin_regex` allows any Chrome extension to make credentialed API calls. Published extensions have fixed IDs — pin to the specific extension ID. | 30 minutes |
| **Archived chunks purge cron** | Archived chunks (`is_archived = true`) accumulate indefinitely until the parent document is hard-deleted. Add an Inngest cron to hard-delete archived chunks older than 90 days to keep table size manageable. | 4 hours |
| **Configurable query log retention per org** | `query_log_retention.py` prunes rows with a hardcoded retention period. Enterprise customers requiring 1-year audit trails will have data silently deleted. Add per-org retention config. | 4 hours |
| **Real-time alerts for moderation FLAG events** | FLAG events are written to `analytics_events` and visible in the admin moderation log, but there is no real-time Slack/email alert. A surge in jailbreak attempts goes unnoticed until admin manually checks. | 4 hours |
| **Atomic API key rotation endpoint** | There is revoke (DELETE) and create (POST) but no atomic rotate endpoint. Callers race between old and new key during manual rotation. Add `POST /api-keys/{id}/rotate`. | 4 hours |
| **API key scoping** | All API keys grant full org-level access. Add a `scopes` field (`["chat:read", "documents:write"]`) so third-party integrators receive principle-of-least-privilege access. | 2 days |
| **Verify Chrome extension + new integrations** | `LEFT_STUFF.md` flags "check the extension" and "drive and others" as known open items. OneDrive, Confluence, GitHub, and Dropbox integrations (added in the most recent sprint) need end-to-end smoke testing. | 1 day |
| **Empty states for admin pages** | Admin analytics, coverage, health, knowledge-gaps, and compliance pages likely show blank content for a brand-new org. Add guidance text + placeholder charts. | 4 hours |
| **Chat sidebar empty state** | No "no conversations yet" state for the conversation list on first login. | 1 hour |
| **Structured logging alert configuration** | No PagerDuty/OpsGenie alerting wired up. No Sentry alert rules documented. No uptime monitoring (Pingdom/Checkly). | 1 day |

---

## Recommended Build Order

### Week 1 — Security & Auth (unblocks safe operation)

**Days 1–2:**
- Fix messages RLS policy (2 hours — single SQL migration)
- Add private IP validation to webhook delivery and agent callbacks (4 hours)
- Tighten CORS to explicit allowlist (30 min)
- Fix API key enumeration timing attack (30 min)
- Replace `detail=str(exc)` leaks in documents.py + public_api.py (2 hours)
- Enforce Inngest signing key in production (1 hour)
- Add startup validation for GEMINI_API_KEY, OAUTH_STATE_SECRET, INNGEST_SIGNING_KEY (2 hours)
- Unicode normalization in moderation (1 hour)
- Confirm query_logs RLS policy (1 hour)

**Days 3–5:**
- Forgot password page + reset flow (1 day)
- Next.js route protection middleware (4 hours)
- Session expiry → redirect to login (4 hours)
- Deep health check with dependency probes (4 hours)
- Dockerfile: non-root USER (30 min)
- OAuth callback error handling (2 hours)

### Week 2 — CI/CD & Core UX gaps

**Days 1–2:**
- GitHub Actions: PR gate with pytest + tsc --noEmit + pnpm build (1 day)
- Member role promotion UI + PATCH /organizations/members/:id endpoint (1 day)

**Days 3–5:**
- In-app password change endpoint + UI (4 hours)
- Fix invite flow for existing Supabase users (1 day)
- Rate limit: document upload init, OAuth endpoints, admin aggregation endpoints (1 day)
- Enforce document upload limits by plan (4 hours)
- Admin analytics pagination: LIMIT clause (4 hours)

### Week 3 — Billing (revenue unlock)

- Stripe integration: checkout session creation, webhook handler (`customer.subscription.created/updated/deleted`), plan sync to `organizations.plan` (2 weeks — but start here, runs in parallel with Week 4)
- `/settings/billing` page: current plan, usage meters, upgrade/downgrade buttons, invoice list
- Upgrade CTA wiring: `QuotaMeter` and `quota_exceeded` 402 response → real billing page
- Seat cap and document cap enforcement tied to plan from Stripe

### Week 4 — Billing (continued) + GDPR + Activation

- Complete Stripe webhook handler testing
- GDPR data export endpoint + UI trigger (2 days)
- Privacy policy + Terms of Service pages (1 day)
- Onboarding activation checklist: guided first-doc-upload, first-chat, invite-teammate milestones (3 days)
- drive_picker_token: remove raw token from response (1 day)

### Week 5 — Retention & Polish

- Notification preferences UI + preferences table (2 days)
- Negative feedback text capture (4 hours)
- Org ownership transfer endpoint + UI (1 day)
- Leave workspace option for members (4 hours)
- Route-level loading.tsx for all remaining pages (4 hours)
- Google OAuth on signup page (2 hours)
- Webhook delivery log UI (1 day)
- OAuth token refresh failure notifications (4 hours)

### Week 6 — Hardening

- Indirect prompt injection defense: chunk XML delimiters (2 days)
- Output PII scanning (2 days)
- Connection pooling + JWT token caching in verify_jwt (2 days)
- Supabase Pro upgrade for automated backups
- Smoke test Chrome extension + OneDrive/Confluence/GitHub/Dropbox integrations
- NPS survey trigger (1 day)

---

## Technical Debt

1. **`enforce_chat_rate_limit` legacy shim** — `rate_limit.py` contains a deprecated function that passes `org_id` as a user identifier. Never called but confusing. Remove.
2. **`uv.lock*` glob in Dockerfile** — should be `uv.lock`, not `uv.lock*`. Minor but sloppy.
3. **Langfuse sample rate hardcoded at 1.0** — `config.py` default. Will accumulate significant cost at scale. Lower to 0.1 via env var before growth phase.
4. **Admin role checking inconsistent** — `admin.py` uses shared `_require_admin()` helper; some `settings.py` routes duplicate the check inline. Consolidate to avoid a missed check creating a privilege escalation path.
5. **Collection tag filters not schema-validated** — `document_collections.tag_filters` stored as unvalidated JSONB. Malformed filters cause 500 at query time rather than 422 at creation. Add Pydantic schema.
6. **Per-request Supabase client creation** — No connection pooling. Acceptable at pre-launch scale, becomes a problem at ~100 concurrent users.
7. **Archived chunks accumulate indefinitely** — `chunks.is_archived = true` rows are never purged until parent document deletion. At orgs with frequent version uploads, this will bloat the `chunks` and `embeddings` tables substantially over time.
8. **`public_api.py` public API rate limit shares monthly org quota** — API key calls and dashboard calls deplete the same monthly counter. A developer hammering the API throttles all team dashboard users.
9. **No query_logs SELECT RLS policy confirmed** — the table stores sensitive query text. Needs explicit verification that an RLS policy exists.
10. **`last_error` on integrations rows never surfaces to admins** — token refresh failures are silently swallowed. The field is written but no notification pipeline consumes it.
11. **Streaming error delivery inconsistent** — `/chat/stream` delivers some errors as structured SSE `error` events and others may reach the stream as bare exceptions. Audit `task_chain_stream.py` exception paths.

---

## Security Risks

| Risk | Severity | Location | Fix |
|------|----------|----------|-----|
| **Messages RLS: any team member reads all org chats** | CRITICAL | `supabase/migrations/002_rls_policies.sql` | Add `AND conversation_id IN (SELECT id FROM conversations WHERE user_id = auth.uid())` to SELECT policy |
| **SSRF: webhook delivery → private IP** | HIGH | `inngest/webhook_functions.py` | DNS-resolving private IP validator before every `httpx` call |
| **SSRF: agent callback → private IP** | HIGH | `inngest/api_trigger_functions.py` | Same validator |
| **Inngest endpoint unsigned if signing key unset** | HIGH | `inngest/client.py` | Startup assertion in production mode |
| **Exception internals in API responses** | MEDIUM | `documents.py` lines 187, 293; `public_api.py` lines 81, 296, 498 | Replace with fixed strings |
| **API key enumeration** | MEDIUM | `public_api.py` `verify_key()` | Unify error message for not-found vs. revoked |
| **Raw Google OAuth token returned to browser** | MEDIUM | `routers/integrations.py` `drive_picker_token` | Proxy through server |
| **CORS wildcard methods + headers** | MEDIUM | `main.py` lines 82–84 | Explicit allowlists |
| **Indirect prompt injection via document content** | MEDIUM | `services/llm/task_chain.py` | XML chunk delimiters + system prompt instruction |
| **Unicode homoglyph bypass in moderation** | LOW | `services/moderation.py` | Add NFKC normalization |
| **Chrome extension CORS too broad** | LOW | `main.py` `allow_origin_regex` | Pin to fixed extension ID |
| **OAuth tokens in plaintext in DB** | LOW | `integrations` table | `pgcrypto` application-level encryption |
| **Document ingestion event: file path not re-validated** | LOW | `inngest/functions.py` | Verify storage path prefix matches org before ingestion |
| **No virus scanning on uploaded files** | LOW | `inngest/functions.py` parser pipeline | Add ClamAV/cloud AV step before parsing |
| **Agent outputs auto-post without human review** | LOW | `inngest/integration_write_functions.py` | Optional human approval gate per agent type |

---

## Scalability Risks

1. **Per-request Supabase client + per-request JWT network call** — `verify_jwt` calls Supabase auth on every authenticated request. At 100 concurrent users this will hit auth API rate limits. Add an in-process LRU cache keyed on SHA-256(token) with 60s TTL.
2. **`GET /admin/analytics` loads 50,000+ rows in-process** — Aggregation happens in Python after a full table scan. Push to a Postgres `GROUP BY` query or add a `LIMIT` + pagination before this causes an OOM event in production.
3. **No connection pooling** — Each request creates a new Supabase client instance. Railway's 512MB container will exhaust connection limits under sustained load. Consider PgBouncer or Supabase's connection pooler.
4. **Hybrid search + Redis cache fan-out at scale** — Each chat turn runs up to 8 parallel hybrid searches. Each hybrid search is a vector + FTS query merged via RRF. At 50 concurrent chat users this is 400 simultaneous Supabase DB queries. Verify pgvector IVFFlat index is probed, not scanned (`ivfflat.probes` setting).
5. **Langfuse sample rate at 1.0** — Traces every LLM call to Langfuse. At scale this doubles outbound network traffic from every chat request. Lower to 0.1–0.3 before growth phase.
6. **Inngest queue saturation** — No rate limit on document upload init. A malicious or malfunctioning org could queue thousands of ingestion jobs, saturating the shared Inngest concurrency limit and delaying all other background jobs.
7. **`chunks` + `embeddings` table unbounded growth from archived rows** — Frequent version uploads accumulate archived rows indefinitely. At a large org with 1,000 documents and weekly version updates, this table will grow by ~50k rows/month with no purge.
8. **No database read replica** — All reads (chat search, document list, admin analytics) hit the single primary. At growth-stage query volume, add a Supabase read replica for analytics and non-latency-sensitive reads.

---

## Cost Optimization Opportunities

### AI Costs
- **Langfuse at 1.0 sample rate** — Every LLM call sends a trace. Lower to 0.1 in production. Estimated 10× reduction in Langfuse storage costs.
- **`chat_max_context_chunks = 20`** — At 1,000 chars/chunk average, this is 20k chars of context per LLM call. Profile actual retrieval precision: if the top-5 chunks already contain the answer, lowering to `chat_max_context_chunks = 10` roughly halves prompt token cost.
- **Embedding cache warm-hit rate** — The 60s Redis TTL on hybrid search results is very short. For repeated queries on the same topic (e.g., "What is our vacation policy?"), increase to 300s. Monitor Langfuse for repeated identical queries and tune accordingly.
- **`CHAT_SEARCH_K = 8` (vector recall)** — Each search retrieves 8 vectors + 20 FTS results, merged to 20 chunks. If recall at k=5 is sufficient, reducing `k` cuts embedding lookup time and token cost.

### Infrastructure Costs
- **Railway: right-size the container** — Default Railway containers are often over-provisioned for early-stage traffic. Profile memory usage under realistic load before committing to a larger plan.
- **Supabase Storage vs. Cloudflare R2** — The codebase already supports `STORAGE_PROVIDER=r2`. R2 has no egress fees; Supabase Storage charges for egress. For document-heavy orgs, R2 will be significantly cheaper at scale.
- **Archived chunk storage** — Vectors are 768 × 4 bytes = ~3KB each. At 10 chunks/document, 1,000 archived documents = 30MB of orphaned embedding data. The purge cron (P3 item) also reduces pgvector index size, which directly improves query speed.

### Storage Costs
- **Supabase Storage document retention** — Documents deleted by users are hard-deleted from storage immediately. No orphaned files accumulate. Good.
- **Query log retention** — `query_log_retention.py` prunes old rows on a schedule. Confirm the retention window is configured appropriately to avoid unbounded table growth.

---

## Final Verdict

**Can this product be launched today?**

**No — but it is 5–6 weeks of focused work away from yes.**

The core product works. The AI retrieval pipeline is well-architected, hybrid search is solid, the tool-use loop is properly bounded, integrations are real, and the admin surface is surprisingly complete for a solo-engineer build. This is not a prototype — it has shipped through 5 major versions with 41 DB migrations and real feature completeness.

What makes it unshippable right now is not the features. It is:

1. **A privacy violation** (messages RLS) that will destroy customer trust the moment an employee accidentally discovers they can read their CEO's AI conversations.
2. **No revenue model** (no billing) — a SaaS with no payment processing is a demo, not a product.
3. **A permanent lockout bug** (no forgot-password) that will generate support tickets on Day 1.
4. **A server-side request forgery** vulnerability that lets any admin-level user probe the internal network.
5. **No deployment safety net** — any commit can break production with zero automated detection.

The path to launch is not building more features. It is fixing four security/reliability issues (≤3 days of work), building billing (2 weeks), and adding CI/CD (1 day). Everything else in this document is sequenced after those five items.

**Prioritized launch checklist:**
- [ ] Fix messages RLS SELECT policy → user-scoped (2 hours)
- [ ] Add SSRF private IP validation to webhook + agent callback delivery (4 hours)
- [ ] Build forgot-password + reset-password flow (1 day)
- [ ] Add Next.js middleware.ts for edge route protection (4 hours)
- [ ] Add CI/CD GitHub Actions with test + typecheck gates (1 day)
- [ ] Build Stripe billing integration + `/settings/billing` page (2 weeks)
- [ ] Enforce Inngest signing key at startup (1 hour)
- [ ] Fix exception internals leaking into API responses (2 hours)

When all eight are done: **launch.**
