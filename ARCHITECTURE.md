# Company Brain — Architecture & Tech Stack Reference

> **Status:** Pre-seed, solo founder + solo engineer  
> **Phase:** Free tier (0 customers) → Paid tier (5–10 customers)  
> **Goal:** AI-powered company brain — centralizes all company knowledge so employees can execute any work task with full company context

---

## Table of Contents

1. [Product Summary](#1-product-summary)
2. [Core Architecture — Tool-Use Pipeline](#2-core-architecture--tool-use-pipeline)
3. [Multi-Tenancy Design](#3-multi-tenancy-design)
4. [FREE Tier Stack](#4-free-tier-stack)
5. [PAID Tier Stack](#5-paid-tier-stack)
6. [Stack Comparison at a Glance](#6-stack-comparison-at-a-glance)
7. [Migration Path: Free → Paid](#7-migration-path-free--paid)
8. [Key Architectural Decisions (ADRs)](#8-key-architectural-decisions-adrs)
9. [Data Models](#9-data-models)
10. [Document Processing Pipeline](#10-document-processing-pipeline)
11. [Folder Structure](#11-folder-structure)
12. [Cost Estimates](#12-cost-estimates)

---

## 1. Product Summary

**Company Brain** is a multi-tenant SaaS that centralizes everything a company knows — SOPs, handbooks, brand guidelines, product info, pricing, culture docs, meeting transcripts, job criteria, project files — into a single AI-powered brain. Employees then use that brain to **execute any work task**: write emails, draft Slack messages, create job descriptions, prep for meetings, respond to customers, onboard new hires, and more. The AI always has full company context, so output matches the company's voice, policies, and knowledge exactly.

**This is not a Q&A tool. It's a work execution platform powered by company context.**

**Core user flows:**
1. Admin uploads all company knowledge (PDFs, DOCX, brand guides, SOPs, culture decks, product docs)
2. System ingests, chunks, embeds, and indexes everything
3. Employee describes a task in natural language: *"Write a follow-up email to a client asking about our refund policy"*
4. LLM uses function calling to decide what company context it needs → searches the knowledge base 2–4 times with targeted queries → gathers relevant chunks
5. LLM generates high-quality output (email, job description, Slack message, etc.) grounded in real company knowledge
6. Employee reviews, edits, copies output
7. Users can upvote/downvote outputs to improve quality over time

**Example tasks the brain enables:**
- "Write a cold email to a potential enterprise client about our product"
- "Draft a Slack announcement about the new parental leave policy"
- "Write a job description for a senior product designer"
- "Help me prep talking points for my investor meeting tomorrow"
- "What should I tell a customer asking about our refund process?"
- "Summarize everything we know about our Q3 goals for a new hire"

---

## 2. Core Architecture — Tool-Use Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    INGESTION PIPELINE                           │
│                                                                 │
│  Document Upload                                                │
│       │                                                         │
│       ▼                                                         │
│  Document Parser ──► Chunker ──► Embedder ──► Vector Store      │
│  (PDF/DOCX/TXT)     (Semantic   (Google/      (pgvector /       │
│                      + Overlap)  OpenAI)       Pinecone)        │
│                           │                                     │
│                           ▼                                     │
│                    Metadata Store (PostgreSQL)                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              TASK EXECUTION PIPELINE (Tool-Use)                 │
│                                                                 │
│  User Task: "Write email to client re: refund policy"           │
│       │                                                         │
│       ▼                                                         │
│  LLM receives task + tool definition                            │
│  [search_company_knowledge(query: str)]                         │
│       │                                                         │
│       │  LLM decides what context it needs:                     │
│       ├──► tool_call("refund policy")          ─┐              │
│       ├──► tool_call("customer email tone")    ─┤              │
│       └──► tool_call("complaint handling SOP") ─┤              │
│                                                  │              │
│       Each tool call runs hybrid search:         │              │
│       Vector Search + FTS → RRF fusion           │              │
│                                                  ▼              │
│                                         All context gathered    │
│                                                  │              │
│                                                  ▼              │
│                                    LLM generates final output   │
│                                    (email / JD / Slack msg etc) │
│                                    grounded in company context  │
└─────────────────────────────────────────────────────────────────┘
```

**Why tool-use over pure RAG:** The user's task description is rarely a good search query. *"Write an email about refunds"* needs to retrieve refund policy, brand voice, AND communication guidelines — three separate searches. The LLM decides what to look up; we execute the searches. One generation pass at the end produces the output.

**Hybrid search** (vector + full-text via RRF) runs inside every tool call. This is non-negotiable — vector alone misses exact product names, policy codes, and numbers.

---

## 3. Multi-Tenancy Design

**Strategy: Shared database, row-level isolation (RLS)**

- Every row in every table has an `org_id` (UUID) column
- Supabase Row Level Security (RLS) policies enforce `org_id = auth.jwt()['org_id']`
- Vector embeddings are namespaced by `org_id` (pgvector filter / Pinecone namespace)
- Storage buckets are prefixed by `org_id`

This is the right pattern for a SaaS at this scale: cheaper than per-tenant DBs, more secure than soft-delete filtering.

```
Organizations (tenants)
    │
    ├── Users (belong to one org)
    ├── Documents (scoped to org)
    ├── Chunks + Embeddings (scoped to org)
    └── Conversations + Messages (scoped to org + user)
```

---

## 4. FREE Tier Stack

> **Goal:** $0/month, production-quality architecture, real users served.

### 4.1 Frontend
| Layer | Choice | Why |
|-------|--------|-----|
| Framework | **Next.js 14+ (App Router)** | Full-stack, RSC, streaming, file-based routing |
| UI Library | **shadcn/ui + Tailwind CSS** | Best-in-class accessible components, zero cost |
| State | **Zustand** | Lightweight, no boilerplate |
| Forms | **React Hook Form + Zod** | Type-safe validation |
| Hosting | **Vercel (Hobby)** | Free, edge network, CI/CD from GitHub |

### 4.2 Backend
| Layer | Choice | Why |
|-------|--------|-----|
| API Framework | **FastAPI (Python 3.12)** | Best AI/ML ecosystem, async, auto-docs |
| Runtime Host | **Railway (Starter)** | $5 free credit/month; doesn't cold-start like Render |
| Task Queue | **Inngest (Free tier)** | 50k events/month; perfect for doc ingestion jobs |
| Caching | **Upstash Redis (Free)** | 10k commands/day; rate limiting + session cache |

> **Why Python backend over Node?** LangChain, LlamaIndex, sentence-transformers, unstructured — the entire AI/ML ecosystem lives in Python. All serious RAG implementations use Python.

### 4.3 Database & Storage
| Layer | Choice | Free Limits |
|-------|--------|-------------|
| PostgreSQL | **Supabase (Free)** | 500 MB database |
| Auth | **Supabase Auth** | 50,000 MAU |
| File Storage | **Supabase Storage** | 1 GB |
| Vector Store | **Supabase pgvector** | Included in 500 MB |
| Full-Text Search | **PostgreSQL FTS (tsvector)** | Built-in |

Supabase free tier is genuinely production-ready for early customers. All four services (DB + Auth + Storage + Vector) in one place = zero cross-service latency, one dashboard.

### 4.4 AI / LLM
| Layer | Choice | Free Limits |
|-------|--------|-------------|
| LLM | **Google Gemini 2.0 Flash** | 1,500 req/day, 1M tokens/min |
| Embeddings | **Google text-embedding-004** | Same generous limits (768 dims) |
| Document Parsing | **unstructured (open-source)** | Self-hosted, free |
| PDF parsing | **PyMuPDF (pymupdf)** | Free, fastest PDF parser |
| DOCX parsing | **python-docx** | Free |

> Gemini 2.0 Flash is genuinely capable for RAG tasks and the free tier is far more generous than any other provider. When you migrate to paid, a 3-line config swap to Claude.

### 4.5 Document Ingestion
| Layer | Choice |
|-------|--------|
| Chunking | **LangChain RecursiveCharacterTextSplitter** (semantic, 512 tokens, 64 overlap) |
| Embedding batch | **Google Batch Embedding API** |
| Async processing | **Inngest functions** (triggered on upload) |
| Progress tracking | **Supabase Realtime** (websocket to frontend) |

### 4.6 Dev Tooling
| Tool | Choice |
|------|--------|
| Monorepo | **Single repo** (no Turborepo overhead yet) |
| Package manager | **pnpm** (frontend) / **uv** (Python) |
| Type checking | **TypeScript strict** + **Pyright** |
| Linting | **Biome** (replaces ESLint + Prettier) |
| Testing | **Vitest** + **Pytest** |
| CI/CD | **GitHub Actions** (free 2000 min/month) |
| Secrets | **Doppler (free tier)** or `.env` files |

---

## 5. PAID Tier Stack

> **Trigger:** 5–10 paying customers. Monthly spend ~$150–300.

### 5.1 Frontend
| Layer | Choice | Cost |
|-------|--------|------|
| Framework | Next.js 14+ (unchanged) | — |
| Hosting | **Vercel Pro** | $20/month |
| Analytics | **PostHog (free up to 1M events)** | $0 |
| Error tracking | **Sentry** | $26/month |

### 5.2 Backend
| Layer | Choice | Cost |
|-------|--------|------|
| API | FastAPI (unchanged) | — |
| Hosting | **Railway Pro** | ~$20–40/month |
| Queue | **Inngest Pro** | $0 (generous free tier scales far) |
| Caching | **Upstash Redis Pro** | ~$10/month |

### 5.3 Database & Storage
| Layer | Choice | Cost |
|-------|--------|------|
| PostgreSQL | **Supabase Pro** | $25/month |
| Auth | Supabase Auth (included) | — |
| File Storage | **Cloudflare R2** | $0.015/GB (no egress fees) |
| Vector Store | **Supabase pgvector** OR **Pinecone Serverless** | $0 (pgvector scales to millions of vectors) |

> **Cloudflare R2 over Supabase Storage for paid:** No egress fees = massive cost savings when customers download documents. Swap storage backend in one config change.

### 5.4 AI / LLM
| Layer | Choice | Cost |
|-------|--------|------|
| LLM (quality) | **Anthropic Claude 3.5 Sonnet** | $3/$15 per 1M tokens in/out |
| LLM (fast/cheap) | **Anthropic Claude 3.5 Haiku** | $0.80/$4 per 1M tokens |
| Embeddings | **OpenAI text-embedding-3-small** | $0.02/1M tokens |
| Document Parsing | **LlamaParse** | $3/1000 pages (handles tables, images, complex PDFs) |
| Reranking | **Cohere Rerank v3** | $2/1000 queries |

> Use Haiku for simple lookups, Sonnet for complex multi-document reasoning. Route by query complexity.

### 5.5 Observability (Paid)
| Tool | Purpose | Cost |
|------|---------|------|
| **Sentry** | Error tracking, performance | $26/month |
| **PostHog** | Product analytics, session replay | $0 up to 1M |
| **Langfuse (cloud)** | LLM traces, cost tracking, eval | Free tier (then $59/month) |
| **Uptime Robot** | Uptime monitoring | Free |

### 5.6 Email & Communication
| Tool | Purpose | Cost |
|------|---------|------|
| **Resend** | Transactional email | Free up to 3k/month, then $20 |
| **React Email** | Email templates | Free |

---

## 6. Stack Comparison at a Glance

```
Component          FREE ($0)                    PAID ($150-300/mo)
─────────────────────────────────────────────────────────────────
Frontend Host      Vercel Hobby                 Vercel Pro ($20)
Backend Host       Railway Starter              Railway Pro ($30)
Database           Supabase Free (500MB)        Supabase Pro ($25)
Auth               Supabase Auth (50k MAU)      Supabase Auth (included)
File Storage       Supabase Storage (1GB)       Cloudflare R2 ($0.015/GB)
Vector DB          Supabase pgvector            Supabase pgvector / Pinecone
LLM                Gemini 2.0 Flash (free)      Claude 3.5 Sonnet/Haiku (pay/token)
Embeddings         Google text-embedding-004    OpenAI text-embedding-3-small
Doc Parsing        PyMuPDF + unstructured       LlamaParse ($3/1000 pages)
Reranker           None (free tier skip)        Cohere Rerank v3
Task Queue         Inngest Free (50k events)    Inngest (scales)
Cache              Upstash Redis Free           Upstash Redis Pro
Observability      GitHub + Vercel logs         Sentry + PostHog + Langfuse
Email              None / manual                Resend ($0-20)
```

---

## 7. Migration Path: Free → Paid

The architecture is designed so each swap is a **config change, not a rewrite**.

```
Step 1: Upgrade Supabase to Pro
  → More storage, no pausing, daily backups, PITR
  → 0 code changes

Step 2: Swap LLM: Gemini → Claude
  → Change LLM_PROVIDER env var
  → Update model instantiation in ai_client.py (one file)
  → 0 business logic changes

Step 3: Swap Embeddings: Google → OpenAI
  → Change EMBEDDING_PROVIDER env var
  → Re-embed all existing chunks (one-time job, ~1hr for 100k chunks)
  → NOTE: must re-embed; can't mix embedding spaces

Step 4: Upgrade Vercel to Pro
  → More bandwidth, team features, preview deployments
  → 0 code changes

Step 5: Add Cloudflare R2 for storage
  → Add R2 bucket, update STORAGE_PROVIDER env var
  → Migrate existing files (one-time script)

Step 6 (optional, >500k vectors): Migrate pgvector → Pinecone
  → Export vectors, import to Pinecone
  → Update vector_store.py adapter
  → 0 business logic changes (adapter pattern abstracts this)
```

**Key principle: Every external service is accessed through an adapter interface. Swap the adapter, not the app.**

---

## 8. Key Architectural Decisions (ADRs)

### ADR-001: Python FastAPI for AI backend (not Node)
- **Decision:** Python FastAPI for all AI/document processing
- **Why:** LangChain, LlamaIndex, sentence-transformers, PyMuPDF, unstructured — the entire AI ecosystem is Python-native. Node wrappers lag by months.
- **Tradeoff:** Two runtimes (Node + Python). Acceptable because Next.js handles UI/BFF, FastAPI handles AI.

### ADR-002: Supabase pgvector over dedicated vector DB (initially)
- **Decision:** Use pgvector in Supabase instead of Pinecone/Qdrant
- **Why:** One less service to manage. pgvector handles millions of vectors well. Joins between vector results and metadata are trivial (same DB).
- **Migration path:** Adapter pattern means swapping to Pinecone is ~50 lines.
- **Tradeoff:** Not as fast as dedicated vector DB at 10M+ vectors. We'll never hit that on free tier.

### ADR-003: Hybrid search from day one
- **Decision:** Always combine vector search (semantic) + PostgreSQL FTS (keyword)
- **Why:** Vector search misses exact matches (product names, codes, SKUs). FTS misses semantic similarity. Hybrid gets both. RRF (Reciprocal Rank Fusion) merges the ranked lists.
- **Tradeoff:** Slightly more complex query. Worth it from the first user.

### ADR-004: Async document processing via job queue
- **Decision:** Document ingestion is always async via Inngest, never synchronous
- **Why:** Parsing a 100-page PDF + generating 300 embeddings takes 30–90 seconds. Never block the HTTP request.
- **Implementation:** Upload → store raw file → queue job → return 202 Accepted → job processes → Supabase Realtime notifies frontend when done.

### ADR-005: Semantic chunking with overlap (not fixed-size)
- **Decision:** RecursiveCharacterTextSplitter, 512 tokens, 64-token overlap
- **Why:** Splits at paragraph/sentence boundaries. Overlap prevents context loss at chunk boundaries. Better retrieval quality than fixed character splits.
- **Chunk metadata stored:** doc_id, page_number, section_heading, chunk_index, char_offset

### ADR-006: Row-level security for multi-tenancy (not separate schemas/DBs)
- **Decision:** Shared Postgres schema, RLS policies on every table
- **Why:** Per-tenant DBs are expensive and operationally complex at this scale. RLS is enforced at the database level — even a bug in application code can't leak cross-tenant data.
- **Implementation:** Every table has `org_id UUID NOT NULL REFERENCES organizations(id)`. Supabase RLS: `USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()))`.

### ADR-007: Citations on all outputs
- **Decision:** All LLM outputs surface which company documents were used as context
- **Why:** Trust mechanism — employees need to know the email/JD/response is grounded in real company policy, not hallucinated. Also surfaces gaps: if the brain couldn't find relevant context, the user knows to upload that document.
- **Implementation:** Sources come from the tool call results, not from the LLM text. Frontend shows "Based on: [doc name]" chips below every output. Less critical for task outputs (emails don't need inline citations) but always shown in the sidebar.

### ADR-008: Conversation memory (windowed, not full history)
- **Decision:** Include last 6 conversation turns in LLM context, not full history
- **Why:** Full history bloats context and increases latency + cost. Most follow-up tasks reference the last 2–3 turns.
- **Future:** Conversation summarization for very long sessions.

### ADR-009: Tool-use (function calling) over pure RAG
- **Decision:** LLM is given a `search_company_knowledge(query)` tool and decides what to search for, rather than us pre-computing a single search from the user's input
- **Why:** Users describe tasks, not search queries. "Write an email about our refund policy" needs to retrieve refund rules, brand voice, AND communication tone — three different searches. The LLM knows what context a good email requires; we don't. Tool-use lets the LLM gather exactly what it needs before generating.
- **Tradeoff:** 2–4 extra search calls per task (runs in parallel, adds ~300–500ms). Worth it for output quality.
- **Scope:** This is lightweight tool-use, NOT a full agent loop. The LLM makes tool calls once, we execute them, it generates. No loops, no self-correction, no autonomous actions.
- **Implementation:** Gemini 2.0 Flash supports function calling on the free tier. Single tool defined: `search_company_knowledge(query: str) → list[chunk]`. Tool calls are parallelized.

---

## 9. Data Models

```sql
-- Core tenant
CREATE TABLE organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  plan TEXT NOT NULL DEFAULT 'free',
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Users (Supabase Auth handles passwords)
CREATE TABLE users (
  id UUID PRIMARY KEY REFERENCES auth.users(id),
  org_id UUID NOT NULL REFERENCES organizations(id),
  role TEXT NOT NULL DEFAULT 'member', -- 'admin' | 'member'
  display_name TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Documents
CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id),
  name TEXT NOT NULL,
  file_path TEXT NOT NULL,       -- Supabase Storage path
  file_type TEXT NOT NULL,       -- 'pdf' | 'docx' | 'txt' | 'md'
  file_size_bytes INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count INTEGER,
  metadata JSONB DEFAULT '{}',
  created_by UUID REFERENCES users(id),
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Chunks (text fragments from documents)
CREATE TABLE chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  chunk_index INTEGER NOT NULL,
  page_number INTEGER,
  section_heading TEXT,
  token_count INTEGER,
  metadata JSONB DEFAULT '{}'
);

-- Vector embeddings (separate table for pgvector)
CREATE TABLE embeddings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  embedding vector(768) NOT NULL   -- 768 for Google, change to 1536 for OpenAI
);

-- Conversations
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id),
  user_id UUID NOT NULL REFERENCES users(id),
  title TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Messages
CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id UUID NOT NULL REFERENCES organizations(id),
  role TEXT NOT NULL,  -- 'user' | 'assistant'
  content TEXT NOT NULL,
  sources JSONB,       -- array of {chunk_id, doc_name, page, excerpt}
  feedback TEXT,       -- 'positive' | 'negative' | null
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX ON chunks USING GIN (content_tsv);
CREATE INDEX ON embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX ON embeddings (org_id);
CREATE INDEX ON documents (org_id, status);
```

---

## 10. Document Processing Pipeline

```
1. Frontend: User uploads file
      │
      ▼
2. Next.js API Route (POST /api/documents/upload)
   - Validate file type + size
   - Upload raw file to Supabase Storage: orgs/{org_id}/docs/{doc_id}/{filename}
   - Insert document row (status = 'pending')
   - Trigger Inngest event: { name: 'doc/uploaded', data: { doc_id, org_id } }
   - Return: { doc_id, status: 'pending' }
      │
      ▼
3. Inngest Function: process-document
   - Download file from storage
   - Parse document → extract text + structure
     - PDF: PyMuPDF (extract text by page)
     - DOCX: python-docx
     - TXT/MD: direct read
   - Update doc status → 'processing'
      │
      ▼
4. Chunking
   - RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)
   - Attach metadata: page_number, section_heading, chunk_index
      │
      ▼
5. Embedding
   - Batch embed all chunks (Google text-embedding-004)
   - Batch size: 100 chunks per API call
      │
      ▼
6. Storage
   - INSERT chunks (content, metadata, tsvector auto-generated)
   - INSERT embeddings (chunk_id, vector)
   - Update document.chunk_count, status → 'ready'
      │
      ▼
7. Supabase Realtime broadcast → Frontend updates status to "Ready"
```

---

## 11. Folder Structure

```
nirnaya/
├── apps/
│   ├── web/                        # Next.js 14 frontend
│   │   ├── app/
│   │   │   ├── (auth)/
│   │   │   │   ├── login/
│   │   │   │   └── signup/
│   │   │   ├── (dashboard)/
│   │   │   │   ├── chat/
│   │   │   │   ├── documents/
│   │   │   │   └── settings/
│   │   │   └── api/               # Next.js API routes (thin BFF)
│   │   ├── components/
│   │   ├── lib/
│   │   └── package.json
│   │
│   └── api/                        # FastAPI Python backend
│       ├── app/
│       │   ├── routers/
│       │   │   ├── documents.py
│       │   │   ├── chat.py
│       │   │   └── search.py
│       │   ├── services/
│       │   │   ├── ingestion/
│       │   │   │   ├── parser.py
│       │   │   │   ├── chunker.py
│       │   │   │   └── embedder.py
│       │   │   ├── retrieval/
│       │   │   │   ├── vector_store.py  # adapter (pgvector / Pinecone)
│       │   │   │   ├── fts.py
│       │   │   │   └── hybrid_search.py
│       │   │   └── llm/
│       │   │       ├── client.py        # adapter (Gemini / Claude)
│       │   │       └── rag_chain.py
│       │   ├── models/
│       │   ├── config.py
│       │   └── main.py
│       ├── inngest/                 # Inngest background functions
│       │   └── process_document.py
│       ├── tests/
│       ├── pyproject.toml
│       └── Dockerfile
│
├── supabase/
│   ├── migrations/                  # SQL migration files
│   └── seed.sql
│
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── deploy.yml
│
├── .env.example
└── README.md
```

---

## 12. Cost Estimates

### FREE Tier (0–10 customers, light usage)
```
Vercel Hobby         $0
Railway Starter      $0 (uses $5 free credit)
Supabase Free        $0
Inngest Free         $0
Upstash Free         $0
Gemini 2.0 Flash     $0 (1500 req/day = very comfortable for early users)
Google Embeddings    $0 (same free tier)
────────────────────────
TOTAL                $0/month
```

### PAID Tier (10–50 customers, moderate usage)
```
Vercel Pro           $20/month
Railway Pro          $30/month (estimated actual compute)
Supabase Pro         $25/month
Cloudflare R2        ~$5/month (100GB storage)
LLM costs (Claude)   ~$30–80/month (depends on usage)
Resend               $0–20/month
Inngest              $0 (generous free tier)
Sentry               $26/month
────────────────────────
TOTAL                ~$136–206/month
Break-even at 2–4 customers paying $50–100/month
```

---

## Quick Reference: Environment Variables

```bash
# AI
GEMINI_API_KEY=
OPENAI_API_KEY=           # paid tier
ANTHROPIC_API_KEY=        # paid tier

# Feature flags (switch providers)
LLM_PROVIDER=gemini       # gemini | claude | openai
EMBEDDING_PROVIDER=google # google | openai

# Supabase
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# Storage
STORAGE_PROVIDER=supabase  # supabase | r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_NAME=

# Inngest
INNGEST_SIGNING_KEY=
INNGEST_EVENT_KEY=

# Upstash Redis
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# App
NEXT_PUBLIC_APP_URL=
API_URL=
```

---

*Last updated: 2026-06-06 | Phase: Pre-seed (Free Tier)*
