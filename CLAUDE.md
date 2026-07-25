# NirnayaIQ — CLAUDE.md

## What This Is

Multi-tenant SaaS **work execution platform** (not a chatbot). Companies upload their knowledge base; employees execute work tasks with full company context. Features: 10 integrations, compliance workflows, background agents, multiplayer channels, MCP server, Chrome extension.

Stack: **Next.js** (App Router, React 19, TS5, Tailwind 4, shadcn/ui) + **FastAPI** (Python 3.12+) + **Supabase** (Postgres + pgvector + Auth + Storage) + **Chrome MV3** extension.

58 DB migrations. Deployed: Vercel (frontend) + Railway (backend, Dockerfile).

---

## Current Status

**Currently on local development.** Tailor responses to local setup — assume services (Supabase, backends, dev servers) may be running locally. Prefer local debugging, avoiding production/staging concerns.

---

## How to Run

```bash
# Frontend
cd apps/web && pnpm dev          # :3000

# Backend
cd apps/api && uv run uvicorn app.main:app --reload  # :8000

# Supabase local
supabase start && supabase db push

# Chrome extension
cd extensions/chrome && pnpm dev  # load unpacked from dist/
```

Env files: `apps/web/.env.local`, `apps/api/.env`. See `.env.example` for all vars.

---

## Architecture Principles — Never Violate

1. **Adapter for every external service.** LLM, embeddings, storage, email — thin wrappers only. `LLM_PROVIDER=claude` in env switches providers without code changes.

2. **Multi-tenancy via RLS, not app logic.** Every table has `org_id`. RLS enforces it at DB level. Use `service_role` client only in Inngest functions, never in HTTP handlers.

3. **Hybrid search on every tool call.** `search_company_knowledge` always runs vector + FTS merged via RRF. Never vector-only.

4. **Tool-use, not pure RAG.** LLM calls `search_company_knowledge(query)` multiple times per turn (max 8 calls, 4 rounds). Never pre-compute a single search from the user message.

5. **Chat is bounded; background agents are autonomous.** Chat: max 4 tool rounds, LLM does not self-correct. Inngest agents (Onboarding, MeetingNotes, PolicyPropagation, VersionDiff) run full autonomous loops.

6. **Document processing is always async.** Never parse/embed in an HTTP handler. Always queue via Inngest (`document/ingest`).

7. **Sources on every output.** "Based on:" panel with document names, not inline citations.

8. **Chat guardrails are constants.** Max 4 rounds / 8 searches / 20 chunks / 16k chars. Change in `task_chain.py` consciously.

---

## Key Locations

```
apps/api/app/
  services/ingestion/   parser → chunker → embedder → pipeline → store
  services/retrieval/   vector_search, fts_search, hybrid_search (RRF), search_cache
  services/llm/         client (provider adapter), task_chain, task_chain_stream
  services/agents/      base_agent + 5 agent types
  services/integrations/ per-provider OAuth + sync modules
  mcp/                  server.py (Streamable HTTP, JSON-RPC 2.0), tools.py (5 tools)
  routers/              25+ routers; chat.py is the main entry point
  inngest/              20 function files; register all in main.py
  auth.py               verify_jwt() FastAPI dependency

apps/web/app/(dashboard)/
  chat/                 main chat + [id] + meeting-prep
  documents/            upload, list, version history
  settings/             profile, team, API keys, integrations, collections, webhooks, templates
  compliance/           pending acks + quiz UI
  admin/                11 pages (analytics, coverage, health, gaps, moderation, etc.)

apps/web/components/    chat/ (30+), documents/ (15+), admin/ (10+), layout/
apps/web/hooks/         18+ hooks — use-chat.ts is the SSE streaming core
extensions/chrome/src/  background.ts (service worker), sidepanel/ (React app)
supabase/migrations/    001 → 058 SQL files
```

---

## Coding Conventions

**Python:** type hints everywhere, Pydantic for all request/response bodies, `async def` on all handlers, `Depends(verify_jwt)` for auth, `inngest.function` + `step.run()` for background jobs.

**TypeScript:** strict mode, `cn()` for classNames, shadcn/ui only, RSC by default (`'use client'` only when needed), never put secrets in `NEXT_PUBLIC_*`, all FastAPI calls go through Next.js proxy routes.

**Both:** no comments unless WHY is non-obvious, no premature abstraction, user-readable error messages at the boundary.

---

## Common Tasks

**New DB table:** `supabase/migrations/0NN_name.sql` → table + RLS policy + indexes → `supabase db push` → type in `apps/web/lib/types.ts`

**New API endpoint:** add to `apps/api/app/routers/` (Pydantic models + `Depends(verify_jwt)`) → Next.js proxy route in `apps/web/app/api/`

**New Inngest job:** add to `apps/api/app/inngest/` → register in `main.py` → trigger via `inngest_client.send({"name": "...", "data": {...}})` → use `step.run()` for retryable chunks

**New integration:** OAuth router → service in `services/integrations/` → Inngest polling function → store tokens in `integrations` table → settings UI in `settings/integrations/page.tsx`

**Switch LLM:** `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY` — `services/llm/client.py` already handles the branch.

**Switch embeddings:** `EMBEDDING_PROVIDER=openai` + `EMBEDDING_DIMENSIONS=1536` → update `embedder.py` → re-embed all chunks → migration to alter vector column type.

---

## Integrations

Two storage shapes coexist: **legacy per-provider tables** (`drive_integrations`, `notion_integrations`, `slack_integrations`, `gmail_integrations`) and **unified `integrations` table** (migration 036, used by OneDrive/Confluence/GitHub/Dropbox/Jira). Shared helpers in `services/integrations/_unified.py`.

External binary ingest event: `doc/process-binary-external` — OneDrive, Confluence, Dropbox queue this; worker downloads bytes and runs the standard pipeline without a Supabase Storage round trip.

Gmail re-auth required if Drive was connected first (scopes don't overlap). Re-auth banner shown when `has_send_scope = false`.

---

## What We're NOT Building

- SSO / SAML
- Reranker (RRF is sufficient)
- LlamaParse (PyMuPDF + python-docx is sufficient)
- Mobile app
- Multi-workspace / sub-orgs
- Real-time collaborative editing

---

## DB Notes

- `chunks.content_tsv` is a **generated column** — never write to it.
- `chunks.is_archived = true` = soft-deleted by version upload. Search RPCs exclude them. Hard-delete only on parent document deletion. No purge job — archived chunks accumulate.
- `conversations.is_channel` + `channel_visibility` added in migration 058 for team channels.
- `knowledge_quizzes`: partial unique — one active quiz per `(document_id, document_version_id)`.

---

## Deployment

- Frontend: push to `main` → Vercel auto-deploys
- Backend: push to `main` → Railway auto-deploys (Docker)
- Migrations: `supabase db push` — manual, test on staging first
- Inngest functions register on backend startup