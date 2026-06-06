# Company Brain — CLAUDE.md

> This is the project context file for Claude Code. Read this before making any changes.

## What This Project Is

**Company Brain** is a multi-tenant SaaS that centralizes everything a company knows (PDFs, SOPs, handbooks, brand guides, product docs, culture decks, meeting transcripts) and lets employees execute any work task — writing emails, drafting job descriptions, creating Slack announcements, answering policy questions, prepping for meetings — with the AI having full company context at all times.

**Not a Q&A tool. A work execution platform powered by company knowledge.**

Stack: Next.js 14 (frontend) + FastAPI Python (AI backend) + Supabase (DB/auth/storage/vector).

Current phase: Pre-seed, free-tier infrastructure, solo engineer (Aniket).

---

## Project Structure

```
nirnaya/
├── apps/
│   ├── web/           Next.js 14 frontend (App Router, TypeScript)
│   └── api/           FastAPI Python backend (AI, RAG, document processing)
├── supabase/
│   └── migrations/    SQL migration files — run via `supabase db push`
├── ARCHITECTURE.md    Full tech stack decisions + data models
├── ROADMAP.md         30-day build plan + pricing
└── CLAUDE.md          This file
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
cd supabase
supabase start    # starts local Postgres + Auth + Storage
supabase db push  # apply migrations
```

**Environment files:**
- `apps/web/.env.local` — Next.js env vars
- `apps/api/.env` — FastAPI env vars
- See `.env.example` at root for all required variables

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui |
| Backend | FastAPI (Python 3.12), Pydantic, uvicorn |
| Database | Supabase PostgreSQL + pgvector |
| Auth | Supabase Auth (email/password) |
| File Storage | Supabase Storage |
| Vector Search | pgvector (cosine similarity, IVFFlat index) |
| Full-Text Search | PostgreSQL tsvector + GIN index |
| LLM (free) | Google Gemini 2.0 Flash |
| Embeddings (free) | Google text-embedding-004 (768 dims) |
| Background Jobs | Inngest |
| Caching | Upstash Redis |
| Frontend Hosting | Vercel |
| Backend Hosting | Railway |

---

## Architecture Principles

**Never violate these:**

1. **Every external service has an adapter.** LLM, embeddings, vector store, and file storage are each accessed through a thin wrapper. Never call provider SDKs directly from business logic. This is how we swap Gemini for Claude in one config change when we have paying customers.

2. **Multi-tenancy via RLS, not app logic.** Every table has `org_id`. Supabase RLS policies enforce it at DB level. Never filter by org_id in application code and rely on that for security — always use RLS.

3. **Hybrid search inside every tool call.** The LLM's `search_company_knowledge` tool always runs vector search + PostgreSQL FTS merged via RRF. Never vector-only. This is the retrieval engine for all outputs.

4. **Tool-use, not pure RAG.** The LLM decides what to search for by calling `search_company_knowledge(query)` 2–4 times. We never pre-compute a single search from the user's message. The LLM knows what context a good email / JD / response requires; we execute the searches it requests.

5. **This is NOT a full agent.** Tool calls happen once per turn. The LLM does not loop, self-correct, or take autonomous actions. User always reviews output before doing anything with it.

6. **Document processing is always async.** Never parse/embed a document in an HTTP request handler. Always queue via Inngest.

7. **Sources on every output.** Every generated output surfaces which documents were used as context. Not inline citations (this isn't a research paper), but a "Based on:" panel showing document names. This builds trust and surfaces knowledge gaps.

---

## Key File Locations (once built)

```
apps/api/app/
├── services/
│   ├── ingestion/
│   │   ├── parser.py           Document parsing (PDF/DOCX/TXT)
│   │   ├── chunker.py          RecursiveCharacterTextSplitter
│   │   └── embedder.py         Google text-embedding-004 adapter
│   ├── retrieval/
│   │   ├── vector_search.py    pgvector cosine search
│   │   ├── fts_search.py       PostgreSQL full-text search
│   │   └── hybrid_search.py    RRF fusion of both — called by the LLM's search tool
│   └── llm/
│       ├── client.py           Gemini/Claude adapter + SEARCH_TOOL definition
│       └── task_chain.py       Tool-use pipeline: LLM calls search → parallel execution → generation
│       └── task_chain_stream.py  Streaming version: emits searching/sources/token/done events
├── routers/
│   ├── documents.py          Upload, list, delete
│   └── chat.py               POST /chat + POST /chat/stream
└── auth.py                   JWT verification (FastAPI dependency)

apps/web/
├── app/
│   ├── (auth)/               login, signup pages
│   ├── (dashboard)/          chat, documents, settings pages
│   └── api/                  Next.js API routes (thin proxies to FastAPI)
├── components/
│   ├── chat/                 MessageList, MessageInput, Citations
│   ├── documents/            UploadDialog, DocumentTable
│   └── layout/               Sidebar, Header
└── hooks/
    ├── use-chat.ts            SSE streaming + conversation state
    ├── use-documents.ts       Document list + SWR
    └── use-document-realtime.ts  Supabase Realtime status updates
```

---

## Environment Variables

```bash
# AI Providers
GEMINI_API_KEY=           # Google AI Studio — free tier
ANTHROPIC_API_KEY=        # Claude — add when switching to paid tier
OPENAI_API_KEY=           # OpenAI — add when switching embeddings

# Provider selection (change these to switch providers without code changes)
LLM_PROVIDER=gemini                 # gemini | claude | openai
EMBEDDING_PROVIDER=google           # google | openai
EMBEDDING_DIMENSIONS=768            # 768 for Google, 1536 for OpenAI

# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=          # backend only, never expose to browser
SUPABASE_JWT_SECRET=                # for JWT verification in FastAPI

# Next.js public vars (safe for browser)
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_API_URL=                # FastAPI URL

# Background jobs
INNGEST_SIGNING_KEY=
INNGEST_EVENT_KEY=

# Caching
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# Storage (add when migrating to R2 on paid tier)
STORAGE_PROVIDER=supabase           # supabase | r2
```

---

## Database Schema Quick Reference

```sql
organizations     id, name, slug, plan, created_at
users             id (= auth.uid), org_id, role, display_name
documents         id, org_id, name, file_path, file_type, status, chunk_count
chunks            id, org_id, document_id, content, content_tsv (auto), chunk_index, page_number
embeddings        id, chunk_id, org_id, embedding vector(768)
conversations     id, org_id, user_id, title
messages          id, conversation_id, org_id, role, content, sources (jsonb), feedback
```

All tables have RLS enabled. `content_tsv` in chunks is a generated column — never write to it.

---

## Coding Conventions

**Python (FastAPI):**
- Python 3.12+, type hints everywhere
- Pydantic models for all request/response bodies
- FastAPI `Depends()` for auth, DB client injection
- `async def` for all route handlers
- Never use `supabase.table().select()` without RLS-scoped client in route handlers
- Use service role client only in Inngest background functions

**TypeScript (Next.js):**
- TypeScript strict mode
- Use `cn()` from `lib/utils.ts` for className merging
- shadcn/ui components only — no installing new UI libraries without a reason
- React Server Components by default; add `'use client'` only when needed
- Never store secrets in `NEXT_PUBLIC_*` vars
- API calls to FastAPI go through Next.js API routes (proxy) — never call FastAPI directly from browser in prod

**Both:**
- No comments unless the WHY is non-obvious
- No premature abstraction — 3 similar lines is fine
- Error messages must be user-readable for anything user-facing

---

## Common Tasks

**Add a new database table:**
1. Create `supabase/migrations/XXX_description.sql`
2. Add table + RLS policy + indexes
3. Run `supabase db push`
4. Add TypeScript type to `apps/web/lib/types.ts`

**Add a new API endpoint:**
1. Add route to relevant file in `apps/api/app/routers/`
2. Add Pydantic request/response models
3. Add `Depends(verify_jwt)` for auth
4. Add Next.js proxy route in `apps/web/app/api/` if frontend needs it

**Switch LLM from Gemini to Claude:**
1. Set `LLM_PROVIDER=claude` in env vars
2. Add `ANTHROPIC_API_KEY=` 
3. Update `apps/api/app/services/llm/client.py` to check `LLM_PROVIDER`
4. No other changes needed

**Switch embeddings from Google to OpenAI:**
1. Set `EMBEDDING_PROVIDER=openai`, `EMBEDDING_DIMENSIONS=1536`
2. Add `OPENAI_API_KEY=`
3. Update `apps/api/app/services/ingestion/embedder.py`
4. **Run re-embedding job** (all existing chunks must be re-embedded — cannot mix 768-dim and 1536-dim vectors)
5. Update `supabase/migrations/` to alter embeddings column: `vector(768) → vector(1536)`

---

## What We're NOT Building in v1

Don't add these — they're explicitly deferred:
- Slack / Notion / Google Drive integrations
- SSO / SAML
- Reranker (Cohere Rerank)
- LlamaParse (using PyMuPDF)
- Audit logs
- Multi-workspace (Business plan feature)
- Mobile app
- Webhooks / public API
- Per-plan rate limiting (handle manually)
- Document versioning

---

## Deployment

**Frontend:** Push to `main` → Vercel auto-deploys
**Backend:** Push to `main` → Railway auto-deploys (configured via `railway.json`)
**Database migrations:** `supabase db push` (manual, run from local)

Production URLs (add once known):
- Frontend: `https://[app].vercel.app`
- Backend: `https://[app].railway.app`
- Supabase project: `https://app.supabase.com/project/[ref]`

---

## Pricing (for reference in UI)

| Plan | Price | Users | Documents | Queries/month |
|------|-------|-------|-----------|---------------|
| Starter | $49/month | 10 | 100 | 500 |
| Team | $129/month | 30 | 1,000 | 3,000 |
| Business | $299/month | Unlimited | Unlimited | Unlimited |

Quarterly discount: ~10% off. Annual discount: ~20% off.

---

## References

- Full architecture decisions: `ARCHITECTURE.md`
- 30-day build roadmap: `ROADMAP.md`
- Supabase docs: https://supabase.com/docs
- FastAPI docs: https://fastapi.tiangolo.com
- Inngest Python SDK: https://inngest.com/docs/sdk/python
- Google Gemini API: https://ai.google.dev/api/python/google/generativeai
