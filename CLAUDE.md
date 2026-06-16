# NirnayaIQ — CLAUDE.md

> Project context file for Claude Code. Read this before making any changes.

## What This Project Is

**NirnayaIQ** is a multi-tenant SaaS work execution platform. Companies upload their entire knowledge base (PDFs, SOPs, handbooks, brand guides, product docs, culture decks, meeting transcripts) and employees use it to execute any work task — writing emails, drafting job descriptions, posting Slack announcements, answering policy questions, prepping for meetings — with the AI always having full company context.

**Not a Q&A chatbot. A work execution platform with AI-powered company context, 5 live integrations, compliance workflows, and autonomous background agents.**

Stack: Next.js (frontend) + FastAPI Python (AI/RAG backend) + Supabase (DB/auth/storage/vector).

Current phase: Pre-seed, free-tier infrastructure, solo engineer (Aniket). Codebase has shipped through v5 + an Agent sprint (35 DB migrations, production-ready).

---

## Project Structure

```
apps/
  api/                   FastAPI Python backend
    app/
      services/          30+ service modules
      routers/           20+ API routers
      inngest/           17 Inngest background job files
      models/            Pydantic models
      middleware/
      auth.py, config.py, database.py, errors.py, main.py
    Dockerfile
    railway.json
    pyproject.toml
    tests/
      retrieval/         Retrieval test suite (FTS, vector, hybrid, RRF)
  web/                   Next.js frontend
    app/
      (auth)/            login, signup, accept-invite
      (dashboard)/       chat, documents, settings, activity, approvals,
                         compliance, insights, help, admin (10 pages)
      api/               60+ Next.js proxy routes to FastAPI
    components/          100+ components (chat 30+, documents 15+, admin 10+)
    hooks/               15+ custom hooks
    lib/                 types, utils, API client, Supabase client
supabase/
  migrations/            35 SQL migration files (001 → 035)
ARCHITECTURE.md          Full tech stack decisions + data models
ROADMAP.md / V2-V5_Roadmap.md / Agent_Roadmap.md
PRICING.md
CLAUDE.md                This file
```

---

## How to Run

**Frontend (Next.js):**
```bash
cd apps/web
pnpm install
pnpm dev          # http://localhost:3000
```

**Backend (FastAPI):**
```bash
cd apps/api
uv sync
uv run uvicorn app.main:app --reload  # http://localhost:8000
```

**Supabase local:**
```bash
supabase start    # starts local Postgres + Auth + Storage
supabase db push  # apply all 35 migrations
```

**Environment files:**
- `apps/web/.env.local` — Next.js env vars
- `apps/api/.env` — FastAPI env vars
- `.env.example` at root for all required variables

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js (App Router), React 19, TypeScript 5, Tailwind CSS 4, shadcn/ui |
| Backend | FastAPI (Python 3.12+), Pydantic, uvicorn |
| Database | Supabase PostgreSQL + pgvector |
| Auth | Supabase Auth (email/password + Google OAuth) |
| File Storage | Supabase Storage (swappable to Cloudflare R2) |
| Vector Search | pgvector (cosine similarity, IVFFlat index, 768 dims) |
| Full-Text Search | PostgreSQL tsvector + GIN index |
| Hybrid Retrieval | RRF (Reciprocal Rank Fusion) merging vector + FTS |
| LLM | Google Gemini 2.0 Flash (default, swappable to Claude/OpenAI) |
| Embeddings | Google text-embedding-004 (768 dims, swappable to OpenAI 1536) |
| Background Jobs | Inngest (17 function files, event-driven orchestration) |
| Caching | Upstash Redis |
| Email | Resend (transactional + inbound forwarding) |
| LLM Observability | Langfuse (tracing, prompt versioning, sample rate) |
| Error Tracking | Sentry |
| Document Parsing | PyMuPDF (PDF), python-docx (DOCX), openpyxl (XLSX), python-pptx (PPTX), beautifulsoup4 (HTML) |
| Frontend Hosting | Vercel |
| Backend Hosting | Railway (Dockerfile + railway.json) |

---

## Architecture Principles

**Never violate these:**

1. **Every external service has an adapter.** LLM, embeddings, vector store, file storage, and email are each accessed through a thin wrapper. Never call provider SDKs directly from business logic. `LLM_PROVIDER=claude` in env is all it takes to switch.

2. **Multi-tenancy via RLS, not app logic.** Every table has `org_id`. Supabase RLS policies enforce it at DB level. Never filter by `org_id` in application code and rely on that for security — always use RLS. Use `service_role` client only in Inngest background functions (never in HTTP handlers).

3. **Hybrid search inside every tool call.** The LLM's `search_company_knowledge` tool always runs vector search + PostgreSQL FTS merged via RRF. Never vector-only. This is the retrieval engine for all chat outputs.

4. **Tool-use, not pure RAG.** The LLM decides what to search for by calling `search_company_knowledge(query)` multiple times per turn (max 8 calls, max 4 tool rounds). We never pre-compute a single search from the user's message.

5. **Chat is NOT a full agent. Background jobs are.** Chat tool calls are bounded per turn — the LLM does not loop or self-correct in chat. Background agents (Inngest) are different: OnboardingAgent, MeetingNotesAgent, PolicyPropagationAgent, SupportResponseAgent, VersionDiffAgent operate autonomously and write their results to the DB.

6. **Document processing is always async.** Never parse/embed a document in an HTTP request handler. Always queue via Inngest (`document/ingest` event).

7. **Sources on every output.** Every generated output surfaces which documents were used as context — a "Based on:" panel with document names, not inline citations. This builds trust and reveals knowledge gaps.

8. **Chat guardrails are constants, not config.** Max 4 tool rounds, max 8 searches, max 20 context chunks, 16k char input limit. These protect latency and cost. Change them in `task_chain.py` consciously, not casually.

---

## Key File Locations

```
apps/api/app/
├── services/
│   ├── ingestion/
│   │   ├── parser.py              PDF/DOCX/TXT/MD/XLSX/PPTX/HTML/CSV/VTT parsing
│   │   ├── chunker.py             RecursiveCharacterTextSplitter (1000 chars / 200 overlap)
│   │   ├── embedder.py            Google text-embedding-004 adapter
│   │   ├── pipeline.py            Orchestrates parse → chunk → embed → store
│   │   └── store.py               Writes chunks + embeddings to Supabase
│   ├── retrieval/
│   │   ├── vector_search.py       pgvector cosine search (k=8)
│   │   ├── fts_search.py          PostgreSQL full-text search (LIMIT 20)
│   │   └── hybrid_search.py       RRF fusion — called by search_company_knowledge tool
│   ├── llm/
│   │   ├── client.py              Gemini/Claude/OpenAI adapter + SEARCH_TOOL definition
│   │   ├── task_chain.py          Tool-use pipeline: LLM → parallel search → generation
│   │   ├── task_chain_stream.py   Streaming variant: emits searching/sources/token/done events
│   │   └── types.py               Message, ToolCall, LLMResponse types
│   ├── agents/
│   │   ├── base_agent.py          Base class (all agents use tool-use, max 4 rounds)
│   │   ├── onboarding_agent.py    First-run workspace setup
│   │   ├── meeting_notes_agent.py Processes meeting transcripts → structured notes
│   │   ├── policy_propagation_agent.py  Applies policies across workspace
│   │   ├── support_response_agent.py    Generates support ticket drafts
│   │   └── version_diff_agent.py  Diffs document versions → summary of changes
│   ├── integrations/
│   │   ├── drive.py               Google Drive OAuth polling + file sync
│   │   ├── notion.py              Export conversations to Notion pages
│   │   ├── slack.py               Slack bot, OAuth, channel posting, event handling
│   │   ├── slack_block_kit.py     Slack Block Kit UI generation
│   │   ├── slack_commands.py      /nirnaya slash command handler
│   │   ├── gmail.py               Gmail OAuth + send via REST API
│   │   ├── email_forward.py       Inbound email → document (Resend webhook)
│   │   └── text_ingest.py         Raw text document ingest
│   ├── analytics.py               Product telemetry event logging
│   ├── activity.py                Team activity feed generation
│   ├── approvals.py               Approval workflow service (email + token-based)
│   ├── auto_tagger.py             Auto-assigns tags to uploaded documents
│   ├── citation_tracker.py        Tracks source documents used per output
│   ├── compliance.py              Policy compliance check service
│   ├── coverage.py                Knowledge base coverage metrics
│   ├── document_summary.py        Auto-generates per-document summaries
│   ├── health_score.py            Document staleness / health scoring
│   ├── intent.py                  Writing vs. analysis intent classification
│   ├── moderation.py              Content moderation (profanity, PII detection)
│   ├── observability.py           Sentry + structured logging setup
│   ├── onboarding.py              Onboarding flow orchestration
│   ├── org_config.py              Per-org AI configuration
│   ├── rate_limit.py              Per-user/per-org rate limiting (Redis-backed)
│   ├── redis_cache.py             Upstash Redis wrapper
│   ├── summarization.py           Conversation summarization
│   ├── time_savings.py            Computes and stores time-saved metrics
│   ├── toc_extractor.py           Extracts table of contents from documents
│   ├── webhooks.py                Outbound webhook dispatch
│   ├── langfuse/__init__.py       LLM tracing + observability
│   ├── agent_registry.py          Registers all agent types
│   ├── agent_callbacks.py         Agent event tracking
│   └── api_keys.py                API key management (hash, validate, revoke)
├── routers/
│   ├── chat.py                    POST /chat, POST /chat/stream, branching, pinning, sharing, export
│   ├── documents.py               Upload (50MB max), list, delete, tag, bulk ops
│   ├── collections.py             Document collection CRUD + scoping
│   ├── templates.py               Saved prompt templates CRUD
│   ├── approvals.py               Approval workflows (submit, resolve, public token endpoint)
│   ├── compliance.py              Compliance acknowledgements
│   ├── slack_router.py            Slack OAuth + slash commands + block kit events
│   ├── gmail_router.py            Gmail OAuth + send endpoint
│   ├── integrations.py            OAuth flows (Drive, Notion, Email forward)
│   ├── public_api.py              /v1 API key-authenticated public endpoints
│   ├── admin.py                   Admin: analytics, coverage, health, knowledge gaps,
│   │                              agent runs, compliance, moderation, support, digests
│   ├── meeting_prep.py            Meeting notes + prep endpoints
│   ├── settings.py                User + org settings
│   ├── invitations.py             Team invitation flow
│   ├── organizations.py           Org management (admin only)
│   ├── team.py                    Team/workspace management
│   ├── document_versions.py       Document version history
│   ├── sharing.py                 Public conversation share links
│   ├── search.py                  Document search endpoint
│   ├── time_savings.py            Time savings analytics endpoint
│   ├── usage.py                   Rate limit status + monthly quota
│   ├── webhooks.py                Outbound webhook management
│   ├── health.py                  Health check
│   └── auth.py                    Supabase auth webhook (org creation on signup)
├── inngest/
│   ├── functions.py               Document ingestion pipeline
│   ├── approval_functions.py      Approval workflow steps + email dispatch
│   ├── compliance_functions.py    Policy compliance checks + Slack notifications
│   ├── feedback_functions.py      User feedback processing
│   ├── gmail_functions.py         Gmail send + delivery tracking
│   ├── integration_functions.py   Drive/Notion/Slack polling
│   ├── integration_write_functions.py  Async writes to integrations
│   ├── knowledge_gap_functions.py Knowledge gap detection + flagging
│   ├── meeting_functions.py       Meeting transcript processing
│   ├── onboarding_functions.py    Onboarding workflows
│   ├── policy_functions.py        Policy propagation + compliance
│   ├── slack_functions.py         Slack message posting
│   ├── support_functions.py       Support ticket generation
│   ├── version_diff_functions.py  Document version diffing
│   ├── webhook_functions.py       Webhook event dispatch
│   └── api_trigger_functions.py   External API-triggered workflows
└── auth.py                        verify_jwt() FastAPI dependency

apps/web/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx         Email/password + Google OAuth
│   │   ├── signup/page.tsx        Org creation flow
│   │   └── accept-invite/page.tsx Invitation acceptance
│   ├── signup-form.tsx            Signup form component (email, password, name, company)
│   └── (dashboard)/
│       ├── layout.tsx             Main layout (sidebar, header, command palette)
│       ├── chat/page.tsx          Default chat / new conversation
│       ├── chat/[id]/page.tsx     Specific conversation
│       ├── chat/meeting-prep/page.tsx  Meeting prep mode
│       ├── documents/page.tsx     Document management (list, filter, upload, tag, bulk)
│       ├── settings/page.tsx      Profile, org settings, team management
│       ├── settings/api/page.tsx  API keys + public API docs
│       ├── settings/integrations/page.tsx  Drive, Notion, Slack, Gmail, Email forward
│       ├── settings/collections/page.tsx   Document collections
│       ├── settings/webhooks/page.tsx      Outbound webhooks
│       ├── activity/page.tsx      Team activity feed
│       ├── approvals/page.tsx     Pending approval queue
│       ├── compliance/pending/page.tsx  Policy acknowledgements
│       ├── insights/page.tsx      Analytics (time saved, knowledge gaps, coverage)
│       ├── help/...               FAQ + help articles
│       └── admin/                 10 admin pages:
│           ├── analytics/         Product telemetry charts
│           ├── coverage/          Knowledge base coverage
│           ├── health/            Document health scores
│           ├── knowledge-gaps/    Under-served queries
│           ├── moderation/        Content moderation logs
│           ├── confidence/        Confidence threshold settings
│           ├── compliance/        Compliance status grid
│           ├── agent-runs/        Agent execution history + logs
│           ├── support/           Support ticket queue
│           └── support/[id]/      Individual ticket
├── components/
│   ├── chat/                      30+ components (messages, input, citations, scope selector,
│   │                              confidence badge, export, Slack post, Gmail send, approvals, etc.)
│   ├── documents/
│   │   ├── upload-context.tsx     Upload queue state (React context)
│   │   ├── upload-widget.tsx      Floating upload progress indicator
│   │   ├── upload-dialog.tsx      Full-screen upload interface
│   │   ├── document-table.tsx     Table view
│   │   ├── document-card-list.tsx Grid view
│   │   ├── document-filters.tsx   Status, type, tags, search, sort
│   │   ├── bulk-action-bar.tsx    Multi-select operations
│   │   └── version-history.tsx    Document version timeline
│   ├── layout/
│   │   ├── sidebar.tsx            Main navigation
│   │   ├── sidebar-nav.tsx        Nav items
│   │   └── header.tsx             Top bar (notifications, user menu)
│   ├── admin/                     Charts, stat cards, agent runs table, compliance report
│   ├── compliance/                Acknowledgement banner + dialog
│   ├── command-palette/           Cmd+K (search conversations, docs, actions)
│   ├── onboarding/                First-run wizard
│   └── ui/                        shadcn/ui primitives
└── hooks/
    ├── use-chat.ts                SSE streaming + conversation state
    ├── use-documents.ts           Document list + SWR
    ├── use-documents-realtime.ts  Supabase Realtime for document status updates
    ├── use-conversations.ts       Conversation list + branching
    ├── use-collections.ts         Document collections
    ├── use-templates.ts           Saved prompt templates
    ├── use-usage.ts               Monthly quota status
    ├── use-keyboard-shortcuts.ts  Cmd+K, Cmd+/, etc.
    └── use-document-ready-toast.ts  Toast when doc finishes processing
```

---

## Environment Variables

```bash
# AI Providers
GEMINI_API_KEY=                   # Google AI Studio — free tier
ANTHROPIC_API_KEY=                # Claude — for switching LLM provider
OPENAI_API_KEY=                   # OpenAI — for switching embeddings

# Provider selection (change to switch providers without code changes)
LLM_PROVIDER=gemini               # gemini | claude | openai
EMBEDDING_PROVIDER=google         # google | openai
EMBEDDING_DIMENSIONS=768          # 768 for Google, 1536 for OpenAI

# Chat tuning (override defaults in task_chain.py)
CHAT_MAX_TOOL_ROUNDS=4
CHAT_MAX_SEARCHES=8
CHAT_MAX_CONTEXT_CHUNKS=20
CHAT_HISTORY_TURNS=10
CHAT_MAX_MESSAGE_CHARS=16000
CHAT_SEARCH_K=8

# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=        # backend only, never expose to browser

# Next.js public vars (safe for browser)
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=              # FastAPI base URL (server-side: API_URL)

# Background jobs
INNGEST_SIGNING_KEY=
INNGEST_EVENT_KEY=

# Caching + rate limiting
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
RATE_LIMIT_CHAT_PER_USER_PER_MINUTE=20
RATE_LIMIT_CHAT_MONTHLY_STARTER=500
RATE_LIMIT_CHAT_MONTHLY_GROWTH=2500

# Email (Resend)
RESEND_API_KEY=
EMAIL_FROM=
EMAIL_RENDER_URL=                 # URL of the Next.js email render route
INTERNAL_EMAIL_SECRET=
INBOUND_EMAIL_DOMAIN=             # For email-forward ingest
INBOUND_EMAIL_WEBHOOK_SECRET=

# Integrations — Google (Drive + Gmail)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=
GMAIL_OAUTH_REDIRECT_URI=

# Integrations — Notion
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
NOTION_OAUTH_REDIRECT_URI=

# Integrations — Slack
SLACK_CLIENT_ID=
SLACK_CLIENT_SECRET=
SLACK_SIGNING_SECRET=
SLACK_OAUTH_REDIRECT_URI=

# OAuth state protection
OAUTH_STATE_SECRET=

# Observability
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.1
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_SAMPLE_RATE=0.3
RELEASE_VERSION=

# Storage (Cloudflare R2 — used when STORAGE_PROVIDER=r2 on paid tier)
STORAGE_PROVIDER=supabase         # supabase | r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=

# Runtime
ENVIRONMENT=production            # development | production
DEBUG=false
LOG_FORMAT=json
LOG_LEVEL=INFO
ALLOWED_ORIGINS=                  # comma-separated CORS origins
```

---

## Database Schema

35 migrations (001 → 035). All tables have RLS enabled with `org_id` on every row.

```sql
-- Core
organizations         id, name, slug, plan, created_at, ai_instructions
users                 id (= auth.uid), org_id, role, display_name, activity_private
documents             id, org_id, name, file_path, file_type, status, chunk_count,
                      health_score, health_label, gap_flag_count, summary, toc
chunks                id, org_id, document_id, content, content_tsv (generated),
                      chunk_index, page_number, section_heading
embeddings            id, chunk_id, org_id, embedding vector(768)
conversations         id, org_id, user_id, title, scoped_document_id,
                      scoped_tags, scoped_collection_id
messages              id, conversation_id, org_id, role, content, sources JSONB,
                      feedback, delivery_status, langfuse_trace_id, metadata JSONB,
                      parent_message_id, is_active_branch, is_pinned

-- Team
invitations           id, org_id, email, inviter_id, token, accepted_at, expires_at

-- Features
document_tags         id, org_id, name, created_at
document_collections  id, org_id, creator_id, name, tag_filters, created_at
templates             id, org_id, creator_id, name, prompt, created_at
shared_conversations  id, conversation_id, token, expires_at, is_public

-- Integrations
integrations          id, org_id, user_id, provider, access_token, refresh_token,
                      metadata JSONB, connected_at
gmail_integrations    id, org_id, user_id, email_address, has_send_scope, connected_at
api_keys              id, org_id, user_id, key_hash, name, last_used_at

-- Compliance & Approvals
compliance_policies   id, org_id, name, document_id, requires_ack, enforcement_date
compliance_acknowledgements  id, org_id, user_id, policy_id, acknowledged_at
approval_workflows    id, org_id, creator_id, item_type, item_id, approvers JSONB,
                      status, change_reason, decided_at
document_drafts       id, org_id, creator_id, document_id, content, status, approver_id

-- Agents & Automation
agent_runs            id, org_id, user_id, agent_type, status, result_json, created_at
meeting_transcripts   id, org_id, uploader_id, file_path, transcript_text, status

-- Observability
analytics_events      id, org_id, user_id, event_type, metadata JSONB, created_at
activity_feed         id, org_id, user_id, activity_type, summary, is_private, created_at
webhooks              id, org_id, event_type, url, metadata JSONB
```

`content_tsv` in `chunks` is a generated column — never write to it directly.

---

## Streaming Chat — Event Types

`POST /chat/stream` emits SSE events as JSON lines:

| kind | payload | description |
|------|---------|-------------|
| `intent` | `{ mode: "writing" \| "analysis" }` | Intent classification result |
| `searching` | `{ query: string }` | LLM issued a search tool call |
| `searched` | `{ query: string, count: number }` | Search returned results |
| `sources` | `{ sources: Source[] }` | Final source documents used |
| `token` | `{ text: string }` | Generated text chunk |
| `confidence` | `{ level: "low" \| "medium" \| "high" }` | Retrieval confidence |
| `final` | `{ content: string, message_id: string }` | Full response (also saved to DB) |
| `error` | `{ message: string }` | Error (stream closes after this) |

---

## Integrations

All integrations are opt-in at org level. OAuth state is verified via `OAUTH_STATE_SECRET`.

| Integration | Type | What it does |
|------------|------|-------------|
| Google Drive | Org OAuth | Polls for new files via Inngest, queues ingestion into knowledge base |
| Notion | Per-user OAuth | Exports conversation outputs as Notion pages |
| Slack | Org OAuth (bot token) | Posts outputs to channels/threads, handles `/nirnaya` slash commands |
| Gmail | Per-user OAuth | Sends email outputs via Gmail REST API, tracks delivery status |
| Email Forward | Inbound (Resend) | Receives forwarded emails, creates documents from email bodies |

Slack notes:
- Slash command: `/nirnaya [query]` → ephemeral response with search results
- Block Kit actions: approve, save as template, post to another channel
- Bot token stored per org workspace in `integrations` table

Gmail notes:
- Scopes: `drive.readonly` + `gmail.send` (requires re-auth if Drive was connected first)
- Re-auth banner shown when scope is missing
- Delivery status tracked on `messages.delivery_status`

---

## Background Agents

Agents are triggered by Inngest events (not HTTP requests). All extend `BaseAgent` and use the same tool-use loop as chat (max 4 rounds). They write their outputs to the DB.

| Agent | Inngest event | Output |
|-------|--------------|--------|
| `OnboardingAgent` | `agent/onboarding` | Sets up workspace config, tags, initial knowledge gaps |
| `MeetingNotesAgent` | `agent/meeting-notes` | Structured notes + action items from transcript |
| `PolicyPropagationAgent` | `agent/policy-propagation` | Applies policy changes across affected documents |
| `SupportResponseAgent` | `agent/support` | Drafts support ticket responses |
| `VersionDiffAgent` | `agent/version-diff` | Summarizes changes between document versions |

---

## Coding Conventions

**Python (FastAPI):**
- Python 3.12+, type hints everywhere
- Pydantic models for all request/response bodies
- `FastAPI Depends()` for auth, DB client injection
- `async def` for all route handlers
- Never call `supabase.table().select()` without RLS-scoped client in HTTP handlers
- Use service role client only in Inngest background functions (never in HTTP handlers)
- All Inngest functions use `inngest.function` decorator + step-based retries

**TypeScript (Next.js):**
- TypeScript strict mode
- `cn()` from `lib/utils.ts` for className merging
- shadcn/ui components only — no new UI libraries without a specific reason
- React Server Components by default; `'use client'` only when needed
- Never put secrets in `NEXT_PUBLIC_*` vars
- All FastAPI calls go through Next.js API proxy routes — never call FastAPI directly from the browser in prod

**Both:**
- No comments unless the WHY is non-obvious
- No premature abstraction — 3 similar lines is fine
- Error messages must be user-readable for anything user-facing

---

## Common Tasks

**Add a new database table:**
1. Create `supabase/migrations/036_description.sql`
2. Add table + RLS policy + relevant indexes (org_id, org_id + status, etc.)
3. Run `supabase db push`
4. Add TypeScript type to `apps/web/lib/types.ts`

**Add a new API endpoint:**
1. Add route to relevant file in `apps/api/app/routers/`
2. Add Pydantic request/response models
3. Add `Depends(verify_jwt)` for auth
4. Add Next.js proxy route in `apps/web/app/api/` if frontend needs it

**Add a new Inngest background job:**
1. Add function to relevant file in `apps/api/app/inngest/`
2. Register it in the Inngest serve handler in `main.py`
3. Trigger with `inngest_client.send({"name": "your/event", "data": {...}})`
4. Use step functions (`step.run()`) for retryable chunks of work

**Add a new integration:**
1. Add OAuth router to `apps/api/app/routers/integrations.py` or new file
2. Add service in `apps/api/app/services/integrations/`
3. Add Inngest polling function in `apps/api/app/inngest/integration_functions.py`
4. Store tokens in `integrations` table (encrypted at rest via Supabase)
5. Add settings UI in `apps/web/app/(dashboard)/settings/integrations/page.tsx`

**Switch LLM from Gemini to Claude:**
1. Set `LLM_PROVIDER=claude` in env
2. Set `ANTHROPIC_API_KEY=`
3. `apps/api/app/services/llm/client.py` already handles the branch — verify it

**Switch embeddings from Google to OpenAI:**
1. Set `EMBEDDING_PROVIDER=openai`, `EMBEDDING_DIMENSIONS=1536`
2. Set `OPENAI_API_KEY=`
3. Update `apps/api/app/services/ingestion/embedder.py`
4. **Run re-embedding job** — cannot mix 768-dim and 1536-dim vectors
5. Add migration: `ALTER TABLE embeddings ALTER COLUMN embedding TYPE vector(1536)`

---

## What We're NOT Building Next

Explicitly deferred — do not implement without a decision:
- SSO / SAML (not needed until Enterprise plan)
- Reranker / Cohere Rerank (RRF fusion is sufficient)
- LlamaParse (PyMuPDF + python-docx is sufficient)
- Mobile app (responsive web covers mobile)
- Multi-workspace / sub-orgs (Business plan feature, future)
- Real-time collaborative editing (Google Docs-style)

Everything else previously listed as "deferred" (Slack, Notion, Drive, webhooks, rate limiting, document versioning, approvals, compliance) **is already implemented**.

---

## Deployment

**Frontend:** Push to `main` → Vercel auto-deploys  
**Backend:** Push to `main` → Railway auto-deploys (Docker build via `railway.json`)  
**Database migrations:** `supabase db push` — manual, run from local, always test on staging first  
**Inngest:** Functions auto-registered on backend startup; deploy key set in Railway env  
**Observability:** Sentry DSN + Langfuse keys set in Railway env; no deploy steps needed

Production checklist:
- [ ] All env vars set in Railway + Vercel dashboards
- [ ] `supabase db push` run against production project
- [ ] Inngest signing key matches between Railway and Inngest dashboard
- [ ] Sentry release version matches `RELEASE_VERSION` env var
- [ ] CORS `ALLOWED_ORIGINS` includes production frontend URL

---

## Pricing (for reference in UI)

| Plan | Price | Users | Documents | Queries/month |
|------|-------|-------|-----------|---------------|
| Starter | $49/month | 10 | 100 | 500 |
| Team | $129/month | 30 | 1,000 | 3,000 |
| Business | $299/month | Unlimited | Unlimited | Unlimited |

Quarterly: ~10% off. Annual: ~20% off. See `PRICING.md` for full details.

---

## References

- Full architecture decisions: `ARCHITECTURE.md`
- Roadmap history: `ROADMAP.md`, `V2_ROADMAP.md` → `V5_Roadmap.md`, `Agent_Roadmap.md`
- Supabase docs: https://supabase.com/docs
- FastAPI docs: https://fastapi.tiangolo.com
- Inngest Python SDK: https://www.inngest.com/docs/sdk/python
- Google Gemini API: https://ai.google.dev/api/python/google/generativeai
- Langfuse docs: https://langfuse.com/docs
- Resend docs: https://resend.com/docs
