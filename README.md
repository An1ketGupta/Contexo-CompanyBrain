# Contexo

> A multi-tenant SaaS **work execution platform** powered by your company's knowledge base.
> Not a Q&A chatbot — an AI co-pilot that drafts emails, writes policies, posts to Slack, and prepares your team for meetings with full company context.

---

## Table of Contents

1. [What Is Contexo?](#what-is-contexo)
2. [Key Features](#key-features)
3. [Tech Stack](#tech-stack)
4. [Architecture Principles](#architecture-principles)
5. [Repository Layout](#repository-layout)
6. [Getting Started](#getting-started)
7. [Environment Variables](#environment-variables)
8. [Database Schema](#database-schema)
9. [The Chat Pipeline](#the-chat-pipeline)
10. [Background Agents](#background-agents)
11. [Integrations](#integrations)
12. [Observability](#observability)
13. [Common Development Tasks](#common-development-tasks)
14. [Deployment](#deployment)
15. [Pricing](#pricing)
16. [Roadmap & Status](#roadmap--status)
17. [Further Reading](#further-reading)

---

## What Is Contexo?

**Contexo** is a SaaS platform where companies upload their entire knowledge base — PDFs, SOPs, handbooks, brand guides, product docs, culture decks, meeting transcripts — and their employees use it to **execute real work**:

- Drafting emails with the right tone and product context
- Writing job descriptions that match company culture
- Posting Slack announcements that follow brand voice
- Answering policy and HR questions
- Prepping for meetings with relevant prior context

Unlike pure RAG chatbots, Contexo is built around the idea that the AI should always have **full company context**, surface its **sources** transparently, route work through **compliance/approval workflows**, and offload long-running tasks to **autonomous background agents**.

The platform is multi-tenant (per-organization), enforces tenant isolation via Postgres Row-Level Security, and integrates with the tools teams already use (Google Drive, Notion, Slack, Gmail, inbound email).

---

## Key Features

### Document Knowledge Base
- Upload PDF, DOCX, TXT, MD, XLSX, PPTX, HTML, CSV, VTT (max 50 MB per file)
- Automatic parsing → chunking → embedding → indexing (vector + FTS)
- Auto-generated per-document **summaries** and **table-of-contents**
- **Health scoring**: stale-doc detection, gap-flag counts, freshness signals
- Document tags, **collections** (saved tag filters), and bulk operations
- Document version history with **VersionDiffAgent** producing change summaries
- Real-time processing status via Supabase Realtime

### Chat / Work Execution
- Streaming responses (SSE) with typed events (`intent`, `searching`, `searched`, `sources`, `token`, `confidence`, `final`)
- **Hybrid retrieval** on every tool call — pgvector cosine + PostgreSQL full-text search, fused with Reciprocal Rank Fusion (RRF)
- **Tool-use loop**, not pure RAG: the LLM decides what to search for, up to 8 searches across max 4 tool rounds per turn
- Scoped conversations: pin a chat to a specific document, tag set, or collection
- Conversation **branching**, **pinning**, **sharing** (public token links), **export**
- **Writing-vs-analysis** intent classification
- **Source panel** ("Based on:") attached to every answer
- **Confidence badges** (low / medium / high) from retrieval confidence signals
- Per-message **feedback** (thumbs + reasons) aggregated by `feedback_functions.py`

### Integrations (5 live)
| Integration | Direction | What it does |
|---|---|---|
| **Google Drive** | Org OAuth → polled | Pulls new files into the knowledge base via Inngest polling |
| **Notion** | Per-user OAuth → outbound | Exports conversation outputs as Notion pages |
| **Slack** | Org OAuth (bot) | Posts outputs to channels/threads + `/nirnaya` slash command + Block Kit actions |
| **Gmail** | Per-user OAuth → outbound | Sends drafted emails via Gmail REST API + delivery tracking |
| **Email Forward** | Inbound (Resend) | Receives forwarded emails, ingests email bodies as documents |

### Compliance, Approvals & Governance
- **Compliance policies** with required acknowledgements per user
- Token-based **approval workflows** with email sign-off (no login required for the approver)
- **Content moderation** (PII detection, profanity)
- **Audit-ready activity feed** with privacy controls per user
- **Org-level AI instructions** (custom system prompt)
- **Rate limits** (Upstash Redis-backed): per-user-per-minute + monthly quota by plan

### Background Agents (4 live, all Inngest-triggered)
- `OnboardingAgent` — first-run workspace setup
- `MeetingNotesAgent` — turns transcripts into structured notes + action items
- `PolicyPropagationAgent` — applies policy updates across affected documents
- `VersionDiffAgent` — summarizes changes between document versions

### Admin & Insights
- 7 admin pages: analytics, health, knowledge health, moderation, confidence tuning, compliance status, agent runs
- **Time-savings analytics** computed per use

### Developer Surface
- API keys with hash-based validation + revocation
- `/v1` public API endpoints (key-authenticated)
- Outbound **webhooks** with event filters

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Next.js (App Router), React 19, TypeScript 5, Tailwind CSS 4, shadcn/ui |
| **Backend** | FastAPI (Python 3.12+), Pydantic, uvicorn |
| **Database** | Supabase PostgreSQL + pgvector |
| **Auth** | Supabase Auth (email/password + Google OAuth) |
| **File Storage** | Supabase Storage (swappable to Cloudflare R2 on paid tier) |
| **Vector Search** | pgvector, cosine similarity, IVFFlat index, 768 dims (Google) — swappable to 1536 (OpenAI) |
| **Full-Text Search** | PostgreSQL `tsvector` + GIN index on a generated `content_tsv` column |
| **Hybrid Retrieval** | Reciprocal Rank Fusion merging vector + FTS results |
| **LLM** | Google Gemini 2.0 Flash (default) — swappable to Claude or OpenAI via env |
| **Embeddings** | Google `text-embedding-004` (768 dims) — swappable to OpenAI (1536 dims) |
| **Background Jobs** | Inngest (17 function files, event-driven, step-based retries) |
| **Caching / Rate Limiting** | Upstash Redis |
| **Email** | Resend (transactional + inbound forwarding) |
| **LLM Observability** | Langfuse (tracing, prompt versioning, sampling) |
| **Error Tracking** | Sentry |
| **Document Parsing** | PyMuPDF (PDF), python-docx (DOCX), openpyxl (XLSX), python-pptx (PPTX), BeautifulSoup (HTML) |
| **Frontend Hosting** | Vercel |
| **Backend Hosting** | Railway (Docker via `railway.json`) |

---

## Architecture Principles

These are load-bearing invariants. **Do not violate them without an explicit decision.**

1. **Every external service has an adapter.** LLM, embeddings, vector store, file storage, and email are all behind thin wrappers. Switching providers is an env-var change, never a code refactor.
2. **Multi-tenancy via RLS, not app logic.** Every table has `org_id`; Supabase RLS enforces it at the DB level. HTTP handlers must use the user-scoped client. `service_role` is only valid inside Inngest functions.
3. **Hybrid search inside every tool call.** The LLM's `search_company_knowledge` tool always runs vector + FTS merged via RRF. Never vector-only.
4. **Tool-use, not pure RAG.** The LLM issues searches based on its plan. Max 8 calls, max 4 tool rounds per turn — capped in `task_chain.py`.
5. **Chat is bounded; background jobs are autonomous.** Chat does not loop or self-correct. Background agents (Inngest) operate freely and persist their results to the DB.
6. **Document processing is always async.** Parsing + embedding never happen inside an HTTP handler — always queue via the `document/ingest` Inngest event.
7. **Every output surfaces its sources.** A "Based on:" panel lists which documents fed the response. No invisible context.
8. **Chat guardrails are constants, not config.** Tool rounds, search counts, context chunk limits, and the 16k character input limit are hard-coded in `task_chain.py` — change them deliberately, not casually.

---

## Repository Layout

```
.
├── apps/
│   ├── api/                  FastAPI Python backend
│   │   ├── app/
│   │   │   ├── services/     30+ service modules (LLM, retrieval, agents, integrations, …)
│   │   │   ├── routers/      20+ HTTP routers
│   │   │   ├── inngest/      17 background-job files
│   │   │   ├── models/       Pydantic models
│   │   │   ├── middleware/
│   │   │   ├── auth.py       verify_jwt() FastAPI dependency
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   ├── errors.py
│   │   │   └── main.py       App factory + Inngest serve mount
│   │   ├── tests/            pytest suite (retrieval suite + admin tests)
│   │   ├── Dockerfile
│   │   ├── railway.json
│   │   └── pyproject.toml
│   └── web/                  Next.js frontend
│       ├── app/
│       │   ├── (auth)/       login, signup, accept-invite
│       │   ├── (dashboard)/  chat, documents, settings, activity, approvals,
│       │   │                 compliance, insights, help, admin (10 sub-pages)
│       │   └── api/          60+ Next.js proxy routes → FastAPI
│       ├── components/       100+ React components
│       ├── hooks/            15+ custom hooks (use-chat, use-documents-realtime, …)
│       └── lib/              types, utils, API client, Supabase client
├── supabase/
│   └── migrations/           35 SQL migration files (001 → 035)
├── extensions/               Local dev extensions / helpers
├── ARCHITECTURE.md           Full tech-stack decision record + data models
├── ROADMAP.md                Original roadmap
├── V2_ROADMAP.md … V5_Roadmap.md   Per-version sprint plans (already shipped)
├── Agent_Roadmap.md          Background-agent sprint plan (already shipped)
├── PRICING.md                Pricing details
├── SMOKE_TEST_GUIDE.md       Manual smoke-test checklist
├── CLAUDE.md                 Project-context file for Claude Code
└── .env.example              Authoritative env-var template
```

For exhaustive file-by-file locations of every service / router / Inngest function / component, see **CLAUDE.md** — it's the canonical map.

---

## Getting Started

### Prerequisites
- **Node.js** 20+ and **pnpm**
- **Python** 3.12+
- **uv** (Python package manager) — `pipx install uv` or see [astral.sh/uv](https://astral.sh/uv)
- **Supabase CLI** — `npm i -g supabase` or [supabase.com/docs/guides/cli](https://supabase.com/docs/guides/cli)
- **Docker** (optional, for the backend container)

### 1. Clone & install

```bash
git clone <repo-url>
cd Product

# Frontend
cd apps/web && pnpm install && cd ../..

# Backend
cd apps/api && uv sync && cd ../..
```

### 2. Configure environment

```bash
cp .env.example .env
cp .env.example apps/web/.env.local
cp .env.example apps/api/.env
```

Fill in real values (see [Environment Variables](#environment-variables)). At a minimum you need: Supabase keys, `GEMINI_API_KEY`, Inngest signing/event keys, and Upstash Redis credentials.

### 3. Start Supabase locally

```bash
supabase start
supabase db push   # applies all 35 migrations
```

This boots local Postgres (with pgvector), Auth, and Storage.

### 4. Run the apps

In two terminals:

```bash
# Backend
cd apps/api
uv run uvicorn app.main:app --reload
# → http://localhost:8000
```

```bash
# Frontend
cd apps/web
pnpm dev
# → http://localhost:3000
```

### 5. (Optional) Run the Inngest dev server

```bash
npx inngest-cli@latest dev -u http://localhost:8000/api/inngest
```

### 6. Smoke test

Sign up → confirm the org is created → upload a small PDF → watch the doc status flip to `ready` → ask a chat question → verify the source panel renders.

For a deeper manual pass, follow `SMOKE_TEST_GUIDE.md`.

---

## Environment Variables

The authoritative list lives in `.env.example`. The key buckets:

```bash
# ─── AI Providers ──────────────────────────────────────────────
GEMINI_API_KEY=
ANTHROPIC_API_KEY=                # only required if LLM_PROVIDER=claude
OPENAI_API_KEY=                   # only required if EMBEDDING_PROVIDER=openai
LLM_PROVIDER=gemini               # gemini | claude | openai
EMBEDDING_PROVIDER=google         # google | openai
EMBEDDING_DIMENSIONS=768          # 768 (Google) | 1536 (OpenAI)

# ─── Chat tuning (overrides in task_chain.py) ──────────────────
CHAT_MAX_TOOL_ROUNDS=4
CHAT_MAX_SEARCHES=8
CHAT_MAX_CONTEXT_CHUNKS=20
CHAT_HISTORY_TURNS=10
CHAT_MAX_MESSAGE_CHARS=16000
CHAT_SEARCH_K=8

# ─── Supabase ──────────────────────────────────────────────────
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=        # backend / Inngest only — never browser
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=              # FastAPI base URL (server-side: API_URL)

# ─── Background jobs ───────────────────────────────────────────
INNGEST_SIGNING_KEY=
INNGEST_EVENT_KEY=

# ─── Caching + rate limiting ───────────────────────────────────
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
RATE_LIMIT_CHAT_PER_USER_PER_MINUTE=20
RATE_LIMIT_CHAT_MONTHLY_STARTER=500
RATE_LIMIT_CHAT_MONTHLY_GROWTH=2500

# ─── Email (Resend) ────────────────────────────────────────────
RESEND_API_KEY=
EMAIL_FROM=
EMAIL_RENDER_URL=
INTERNAL_EMAIL_SECRET=
INBOUND_EMAIL_DOMAIN=
INBOUND_EMAIL_WEBHOOK_SECRET=

# ─── Integrations ──────────────────────────────────────────────
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=
GMAIL_OAUTH_REDIRECT_URI=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
NOTION_OAUTH_REDIRECT_URI=
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
SLACK_SIGNING_SECRET=
SLACK_OAUTH_REDIRECT_URI=
OAUTH_STATE_SECRET=

# ─── Observability ─────────────────────────────────────────────
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.1
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_SAMPLE_RATE=0.3
RELEASE_VERSION=

# ─── Storage (Cloudflare R2 for paid tier) ─────────────────────
STORAGE_PROVIDER=supabase         # supabase | r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=

# ─── Runtime ───────────────────────────────────────────────────
ENVIRONMENT=production            # development | production
DEBUG=false
LOG_FORMAT=json
LOG_LEVEL=INFO
ALLOWED_ORIGINS=                  # comma-separated CORS origins
```

> ⚠ **Never put secrets in `NEXT_PUBLIC_*` vars** — anything with that prefix ships to the browser. `SUPABASE_SERVICE_ROLE_KEY` in particular is **backend-only**.

---

## Database Schema

35 migrations under `supabase/migrations/` (`001_*.sql` → `035_*.sql`). Every table has `org_id` and an RLS policy.

```
─── Core ─────────────────────────────────────────────────────────
organizations              id, name, slug, plan, ai_instructions, created_at
users                      id (= auth.uid), org_id, role, display_name, activity_private
documents                  id, org_id, name, file_path, file_type, status,
                           chunk_count, health_score, health_label, gap_flag_count,
                           summary, toc
chunks                     id, org_id, document_id, content, content_tsv (generated),
                           chunk_index, page_number, section_heading
embeddings                 id, chunk_id, org_id, embedding vector(768)
conversations              id, org_id, user_id, title, scoped_document_id,
                           scoped_tags, scoped_collection_id
messages                   id, conversation_id, org_id, role, content, sources JSONB,
                           feedback, delivery_status, langfuse_trace_id, metadata JSONB,
                           parent_message_id, is_active_branch, is_pinned

─── Team ─────────────────────────────────────────────────────────
invitations                id, org_id, email, inviter_id, token, accepted_at, expires_at

─── Features ─────────────────────────────────────────────────────
document_tags              id, org_id, name, created_at
document_collections       id, org_id, creator_id, name, tag_filters, created_at
templates                  id, org_id, creator_id, name, prompt, created_at
shared_conversations       id, conversation_id, token, expires_at, is_public

─── Integrations ─────────────────────────────────────────────────
integrations               id, org_id, user_id, provider, access_token, refresh_token,
                           metadata JSONB, connected_at
gmail_integrations         id, org_id, user_id, email_address, has_send_scope, …
api_keys                   id, org_id, user_id, key_hash, name, last_used_at

─── Compliance & Approvals ───────────────────────────────────────
compliance_policies        id, org_id, name, document_id, requires_ack, enforcement_date
compliance_acknowledgements id, org_id, user_id, policy_id, acknowledged_at
approval_workflows         id, org_id, creator_id, item_type, item_id,
                           approvers JSONB, status, change_reason, decided_at
document_drafts            id, org_id, creator_id, document_id, content, status, approver_id

─── Agents & Automation ──────────────────────────────────────────
agent_runs                 id, org_id, user_id, agent_type, status, result_json
meeting_transcripts        id, org_id, uploader_id, file_path, transcript_text, status

─── Observability ────────────────────────────────────────────────
analytics_events           id, org_id, user_id, event_type, metadata JSONB
activity_feed              id, org_id, user_id, activity_type, summary, is_private
webhooks                   id, org_id, event_type, url, metadata JSONB
```

> `chunks.content_tsv` is a **generated column** — never write to it. The trigger keeps it in sync with `content`.

---

## The Chat Pipeline

`POST /chat/stream` (proxied through Next.js) returns a Server-Sent Events stream. Each line is JSON with a `kind` and `payload`:

| `kind` | `payload` | When emitted |
|---|---|---|
| `intent` | `{ mode: "writing" \| "analysis" }` | Right after intent classification |
| `searching` | `{ query: string }` | LLM issued a search tool call |
| `searched` | `{ query: string, count: number }` | Search results returned |
| `sources` | `{ sources: Source[] }` | Final source list for the response |
| `token` | `{ text: string }` | Each generated text chunk |
| `confidence` | `{ level: "low" \| "medium" \| "high" }` | Retrieval confidence resolved |
| `final` | `{ content: string, message_id: string }` | Complete response (also persisted) |
| `error` | `{ message: string }` | Stream closes after this |

### Tool-use loop (in `apps/api/app/services/llm/task_chain.py`)

```
user message
   │
   ▼
intent classifier ──► "writing" or "analysis"
   │
   ▼
LLM (round 1) ───► may call search_company_knowledge(query) × N
                       │
                       ▼
                  hybrid_search → vector (pgvector) + FTS (tsvector)
                                  merged via RRF
                       │
                       ▼
                  top-K chunks → context window
   │
   ▼
LLM (rounds 2–4, capped) ──► more searches if it wants
   │
   ▼
final answer (streamed token-by-token)
   │
   ▼
persist message + sources + langfuse_trace_id
```

Guardrails (all in `task_chain.py`):
- **Max 4 tool rounds** per turn
- **Max 8 searches** per turn
- **Max 20 context chunks** in the final prompt
- **16k character** input cap
- Last **10 conversation turns** of history

---

## Background Agents

All agents extend `BaseAgent` (`apps/api/app/services/agents/base_agent.py`) and use the same tool-use loop as chat (4 rounds max). They are triggered by Inngest events — never by HTTP — and persist results to `agent_runs`.

| Agent | Inngest event | Output |
|---|---|---|
| `OnboardingAgent` | `agent/onboarding` | Workspace config + initial tags + first knowledge-gap pass |
| `MeetingNotesAgent` | `agent/meeting-notes` | Structured notes + action items from a transcript |
| `PolicyPropagationAgent` | `agent/policy-propagation` | Cascades policy changes across documents |
| `VersionDiffAgent` | `agent/version-diff` | Human-readable diff between two doc versions |

The full Inngest set (17 files in `apps/api/app/inngest/`) also covers: document ingestion, approvals, compliance, feedback processing, integration polling, knowledge-gap detection, webhooks, and API-triggered workflows.

---

## Integrations

All integrations are **opt-in at the org level**. OAuth state is signed with `OAUTH_STATE_SECRET`. Tokens are stored in `integrations` (encrypted at rest by Supabase).

### Google Drive
- **Scope**: `drive.readonly`
- Org-level OAuth; Inngest polls for new/changed files and queues them for ingestion.

### Notion
- Per-user OAuth.
- Pushes conversation outputs as new Notion pages.

### Slack
- Org-level OAuth (bot token).
- `/nirnaya [query]` slash command → ephemeral search result.
- Block Kit actions: **approve**, **save as template**, **post to another channel**.
- Bot tokens stored per workspace in `integrations`.

### Gmail
- **Scopes**: `drive.readonly` + `gmail.send` (re-auth banner appears if Drive was connected first without `gmail.send`).
- Per-user OAuth.
- Sends drafts via Gmail REST API.
- Delivery state tracked in `messages.delivery_status`.

### Email Forward (inbound via Resend)
- Forward to `<slug>@<INBOUND_EMAIL_DOMAIN>` → Resend webhook → document creation.
- HMAC-verified with `INBOUND_EMAIL_WEBHOOK_SECRET`.

---

## Observability

- **Sentry** — error tracking on both apps. `SENTRY_TRACES_SAMPLE_RATE=0.1` by default.
- **Langfuse** — every LLM call is traced; `messages.langfuse_trace_id` links a chat message to its trace. `LANGFUSE_SAMPLE_RATE=0.3` keeps cost predictable.
- **Structured logs** — `LOG_FORMAT=json` for prod, plain text in dev.
- **`analytics_events`** — product telemetry written by `services/analytics.py`.
- **`activity_feed`** — per-user activity stream (respects `users.activity_private`).
- **Admin → Analytics / Health / Knowledge Health** — UI on top of the above.

---

## Common Development Tasks

### Add a new database table
1. Create `supabase/migrations/036_<description>.sql`.
2. Add the table, **RLS policy**, and relevant indexes (`org_id`, `org_id + status`, etc.).
3. `supabase db push`.
4. Add the TypeScript type to `apps/web/lib/types.ts`.

### Add a new API endpoint
1. Add a route to a file under `apps/api/app/routers/`.
2. Define Pydantic request/response models.
3. Inject `Depends(verify_jwt)` for auth.
4. If the frontend needs it, add a Next.js proxy route in `apps/web/app/api/`.

### Add an Inngest background job
1. Add a function to a file under `apps/api/app/inngest/`.
2. Register it with the Inngest serve handler in `app/main.py`.
3. Trigger via `inngest_client.send({"name": "your/event", "data": {...}})`.
4. Break retryable work into `step.run(...)` chunks.

### Add a new integration
1. OAuth router in `apps/api/app/routers/integrations.py` (or a new file).
2. Service module in `apps/api/app/services/integrations/`.
3. Polling logic in `apps/api/app/inngest/integration_functions.py`.
4. Settings UI in `apps/web/app/(dashboard)/settings/integrations/page.tsx`.

### Switch LLM provider (Gemini → Claude)
1. `LLM_PROVIDER=claude`
2. `ANTHROPIC_API_KEY=...`
3. `services/llm/client.py` branches on the env — no code change needed.

### Switch embeddings (Google → OpenAI)
1. `EMBEDDING_PROVIDER=openai`, `EMBEDDING_DIMENSIONS=1536`, `OPENAI_API_KEY=...`
2. Migration: `ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector(1536);`
3. **Re-embed everything** — you cannot mix 768 and 1536 vectors.

---

## Coding Conventions

### Python (FastAPI)
- Python 3.12+, type hints everywhere.
- Pydantic models for every request/response body.
- `async def` for all route handlers.
- Use `Depends()` for auth and DB-client injection.
- **HTTP handlers**: user-scoped Supabase client. **Inngest functions**: `service_role` client.
- All Inngest functions use `@inngest.function` + `step.run(...)` for retries.

### TypeScript (Next.js)
- Strict mode on.
- `cn()` from `lib/utils.ts` for class merging.
- **shadcn/ui only** — no new UI libraries without a specific reason.
- React Server Components by default; `'use client'` only when needed.
- All FastAPI calls go through Next.js API proxy routes — never call the FastAPI URL directly from the browser in production.

### Both
- **No comments unless the WHY is non-obvious.**
- No premature abstraction — three similar lines is fine.
- Error messages must be user-readable wherever they surface in the UI.

---

## Deployment

| Surface | Where | How |
|---|---|---|
| Frontend | **Vercel** | Push to `main` → auto-deploy |
| Backend | **Railway** | Push to `main` → Docker build (see `railway.json`) |
| DB migrations | **Supabase (prod project)** | `supabase db push` — manual, run from local, test on staging first |
| Inngest | Auto-registered on backend boot | Set `INNGEST_SIGNING_KEY` in Railway |
| Observability | Sentry + Langfuse | DSN/keys in Railway env — no deploy steps |

**Production checklist**
- [ ] All env vars set in Railway + Vercel dashboards
- [ ] `supabase db push` against the production project
- [ ] Inngest signing key matches between Railway and the Inngest dashboard
- [ ] `RELEASE_VERSION` matches your Sentry release
- [ ] `ALLOWED_ORIGINS` includes the production frontend URL

---

## Pricing

| Plan | Price | Users | Documents | Queries / month |
|---|---|---|---|---|
| **Starter** | $49 / month | 10 | 100 | 500 |
| **Team** | $129 / month | 30 | 1,000 | 3,000 |
| **Business** | $299 / month | Unlimited | Unlimited | Unlimited |

Quarterly: ~10% off. Annual: ~20% off. Details in `PRICING.md`.

---

## Roadmap & Status

Current phase: **pre-seed, free-tier infrastructure, solo engineer**. Codebase has shipped through **v5 + the Agent sprint** — 35 DB migrations, 5 live integrations, 5 background agents, 20+ routers, 100+ components.

**Explicitly not building next** (deferred — do not implement without an explicit decision):
- SSO / SAML (Enterprise-plan feature)
- A reranker (RRF fusion is good enough for now)
- LlamaParse (PyMuPDF + python-docx are good enough)
- A native mobile app (responsive web covers it)
- Multi-workspace / sub-orgs (Business-plan feature, future)
- Real-time collaborative editing

Already shipped (do not re-build): Slack, Notion, Drive, Gmail, inbound email, webhooks, rate limiting, document versioning, approvals, compliance.

---

## Further Reading

- **`CLAUDE.md`** — canonical project-context file; the single best place to start
- **`ARCHITECTURE.md`** — full tech-stack decision record and data models
- **`ROADMAP.md`** and **`V2_ROADMAP.md` → `V5_Roadmap.md`** — sprint history
- **`Agent_Roadmap.md`** — background-agent sprint plan
- **`PRICING.md`** — pricing rationale
- **`SMOKE_TEST_GUIDE.md`** — manual QA pass

External docs you'll touch often:
- [Supabase](https://supabase.com/docs) · [FastAPI](https://fastapi.tiangolo.com) · [Inngest (Python)](https://www.inngest.com/docs/sdk/python)
- [Google Gemini API](https://ai.google.dev/api/python/google/generativeai) · [Langfuse](https://langfuse.com/docs) · [Resend](https://resend.com/docs)

---

© Contexo — multi-tenant SaaS work execution platform.
