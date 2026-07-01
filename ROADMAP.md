# Nirnaya IQ — 30-Day Build Roadmap + Pricing Plans

> **Solo engineer, $0 budget, production-quality MVP in 30 days.**
> Everything is sequenced so each day builds directly on the previous one.
> Skipped (not critical for v1): reranker, LlamaParse, SSO, Slack/Notion integrations, audit logs, mobile.

---

## What We're Skipping in v1 (and Why)

| Feature | Skipped Because |
|---------|----------------|
| Reranker (Cohere) | Adds latency + cost; hybrid search is good enough early |
| LlamaParse | PyMuPDF handles 95% of PDFs; pay $3/1000 pages only when needed |
| SSO / SAML | No enterprise customer yet |
| Slack / Notion integration | Connectors are a v2 growth feature |
| Audit logs | Compliance need comes with enterprise |
| API rate limiting per plan | Handle manually until needed |
| Mobile app | Browser is fine for internal tools |
| Analytics dashboard | PostHog free tier + manual for now |
| Document versioning | Storage is cheap but complexity is high |
| Webhooks | No one needs this in v1 |

---

## 30-Day Roadmap

> **Daily time commitment: 4–8 hours**
> Each day ends with a specific, testable deliverable.
> Tools needed before Day 1: VS Code, Git, Node.js 20+, Python 3.12

---

### WEEK 1 — Foundation (Days 1–7)
*Goal: All infrastructure wired up, data flows from upload to vector store.*

---

#### DAY 1 — Project Setup & All Service Accounts
**Time: ~5 hours | Deliverable: Repo exists, all accounts created, local env runs**

**Morning (2.5 hrs) — Accounts & Tooling**
1. Install tools if not present:
   ```
   Node.js 20 LTS (nodejs.org)
   Python 3.12 (python.org)
   pnpm: npm install -g pnpm
   uv: pip install uv
   Supabase CLI: winget install Supabase.CLI
   ```
2. Create accounts (all free):
   - **GitHub** — create repo `nirnaya` (private), initialize with README
   - **Supabase** — supabase.com, create project `nirnaya-prod` in region closest to you
   - **Railway** — railway.app, connect GitHub
   - **Vercel** — vercel.com, connect GitHub
   - **Inngest** — inngest.com, create account + app `nirnaya`
   - **Upstash** — upstash.com, create Redis database (free tier)
   - **Google AI Studio** — aistudio.google.com, generate API key (free Gemini access)

3. Clone repo locally, set up folder structure:
   ```
   nirnaya/
   ├── apps/
   │   ├── web/        ← Next.js (create today)
   │   └── api/        ← FastAPI (create today)
   └── supabase/       ← migrations (create today)
   ```

**Afternoon (2.5 hrs) — App Scaffolding**
4. Initialize Next.js app:
   ```
   cd apps
   pnpm create next-app@latest web --typescript --tailwind --app --no-src-dir
   ```
5. Initialize FastAPI app manually:
   ```
   cd apps/api
   uv init
   uv add fastapi uvicorn python-dotenv supabase google-generativeai \
          langchain-text-splitters pymupdf python-docx inngest
   ```
6. Create root `.env.example` with all variable names (values empty):
   ```
   SUPABASE_URL=
   SUPABASE_ANON_KEY=
   SUPABASE_SERVICE_ROLE_KEY=
   GEMINI_API_KEY=
   INNGEST_SIGNING_KEY=
   INNGEST_EVENT_KEY=
   UPSTASH_REDIS_REST_URL=
   UPSTASH_REDIS_REST_TOKEN=
   LLM_PROVIDER=gemini
   EMBEDDING_PROVIDER=google
   NEXT_PUBLIC_SUPABASE_URL=
   NEXT_PUBLIC_SUPABASE_ANON_KEY=
   ```
7. Copy `.env.example` → `.env.local` (web) and `.env` (api), fill in real keys from step 2
8. Create `.gitignore` — ensure `.env*` files are excluded
9. First commit: `git add . && git commit -m "chore: project scaffolding"`
10. Push to GitHub: `git push origin main`

**End of Day Check:** `cd apps/api && uv run uvicorn app.main:app --reload` should show FastAPI running. `cd apps/web && pnpm dev` should show Next.js running.

---

#### DAY 2 — Database Schema & Supabase Setup
**Time: ~6 hours | Deliverable: All tables exist, RLS active, pgvector enabled**

**Morning (3 hrs) — Supabase Init & Migrations**
1. Initialize Supabase locally:
   ```
   cd nirnaya/supabase
   supabase init
   supabase login
   supabase link --project-ref <your-project-ref>
   ```
2. Enable pgvector extension in Supabase Dashboard:
   - Database → Extensions → search "vector" → Enable
3. Create migration file: `supabase/migrations/001_initial_schema.sql`
   Write all tables exactly as in ARCHITECTURE.md section 9:
   - `organizations`
   - `users` (references auth.users)
   - `documents`
   - `chunks` (with tsvector generated column)
   - `embeddings` (vector(768) column)
   - `conversations`
   - `messages`
   - All indexes (GIN on tsvector, IVFFlat on embeddings, org_id indexes)

4. Create migration file: `supabase/migrations/002_rls_policies.sql`
   Write RLS for every table:
   ```sql
   ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
   ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
   -- etc for all tables
   
   -- Example policy for documents:
   CREATE POLICY "Users see own org docs"
     ON documents FOR ALL
     USING (org_id = (SELECT org_id FROM users WHERE id = auth.uid()));
   ```

**Afternoon (3 hrs) — Storage & Deploy**
5. Create storage bucket in Supabase Dashboard:
   - Storage → New Bucket → name: `documents` → Private bucket
   - Add storage policy: users can only read/write their org's folder

6. Push migrations to remote:
   ```
   supabase db push
   ```
7. Verify in Supabase Dashboard → Table Editor that all tables are created
8. Test RLS: In SQL Editor, run a query as a fake user and verify it returns 0 rows

9. Create `supabase/seed.sql` with one test organization and user for development
10. Commit: `git commit -m "feat: database schema + RLS policies"`

**End of Day Check:** All 7 tables visible in Supabase Dashboard with correct columns. pgvector extension shows as "Enabled".

---

#### DAY 3 — Authentication Flow
**Time: ~5 hours | Deliverable: Users can sign up and log in, org is created on signup**

**Morning (2.5 hrs) — Supabase Auth Setup**
1. Supabase Dashboard → Authentication → Settings:
   - Disable email confirmation for development (Site URL: http://localhost:3000)
   - Enable email provider
2. Install auth packages in Next.js:
   ```
   cd apps/web
   pnpm add @supabase/ssr @supabase/supabase-js
   ```
3. Create Supabase client helpers:
   - `lib/supabase/client.ts` — browser client (`createBrowserClient`)
   - `lib/supabase/server.ts` — server client (`createServerClient` with cookies)
   - `lib/supabase/middleware.ts` — session refresh middleware

4. Create `middleware.ts` at root of `apps/web`:
   - Calls Supabase session refresh on every request
   - Redirects unauthenticated users from `/dashboard/*` to `/login`
   - Redirects authenticated users from `/login`, `/signup` to `/dashboard`

**Afternoon (2.5 hrs) — Auth Pages**
5. Create `/app/(auth)/login/page.tsx`:
   - Email + password fields
   - "Sign in with Email" button
   - Link to signup
   - Error message display
   - Uses `supabase.auth.signInWithPassword()`

6. Create `/app/(auth)/signup/page.tsx`:
   - Full name + company name + email + password fields
   - On submit:
     a. `supabase.auth.signUp()` — creates auth user
     b. Call POST `/api/auth/complete-signup` — creates org + user rows in DB
   - Redirect to `/dashboard` on success

7. Create `/app/api/auth/complete-signup/route.ts` (Next.js API route):
   - Receives: `{ full_name, company_name, user_id }`
   - Uses service role client to:
     a. INSERT into `organizations` (name = company_name, slug = slugify(company_name))
     b. INSERT into `users` (id = user_id, org_id, display_name = full_name, role = 'admin')
   - Returns: `{ org_id }`

8. Create `/app/auth/callback/route.ts` — handles Supabase OAuth callback (needed for email magic links)

9. Test: Sign up → check Supabase Auth + organizations + users tables are all populated

**End of Day Check:** Open localhost:3000/signup → fill form → submit → see redirect to /dashboard (404 is fine, page doesn't exist yet). Verify 3 DB rows created (auth.users, organizations, users).

---

#### DAY 4 — FastAPI Backend Foundation
**Time: ~6 hours | Deliverable: FastAPI deployed on Railway, accessible from browser**

**Morning (3 hrs) — App Structure**
1. Create FastAPI app structure:
   ```
   apps/api/app/
   ├── main.py           ← FastAPI app + CORS + routers
   ├── config.py         ← Settings from env vars (pydantic-settings)
   ├── database.py       ← Supabase client (service role)
   ├── auth.py           ← JWT verification middleware
   ├── routers/
   │   ├── health.py     ← GET /health
   │   └── documents.py  ← placeholder
   └── services/         ← empty for now
   ```

2. `main.py`:
   - FastAPI app with title, version
   - CORS middleware: allow `http://localhost:3000` + production URL
   - Include health router
   - Include documents router (placeholder)

3. `auth.py` — JWT verification:
   - Extract Bearer token from Authorization header
   - Verify with Supabase JWT secret (`SUPABASE_JWT_SECRET` from Dashboard → API Settings)
   - Return decoded payload with `user_id`, `org_id`
   - Raise 401 if invalid
   - Inject as FastAPI dependency: `current_user: dict = Depends(verify_jwt)`

4. `database.py`:
   - `get_supabase_client()` → service role client (for background jobs)
   - `get_user_client(jwt_token)` → user-scoped client (for user actions, respects RLS)

**Afternoon (3 hrs) — Deploy to Railway**
5. Create `apps/api/Dockerfile`:
   ```dockerfile
   FROM python:3.12-slim
   WORKDIR /app
   COPY pyproject.toml .
   RUN pip install uv && uv sync
   COPY . .
   CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```
6. Create `railway.json`:
   ```json
   { "build": { "builder": "DOCKERFILE" }, "deploy": { "startCommand": "..." } }
   ```
7. Go to Railway dashboard:
   - New project → Deploy from GitHub → select `nirnaya` repo → select `apps/api` as root
   - Add all env vars from `.env` to Railway Variables
   - Deploy and wait for build

8. Test: `curl https://your-railway-url.railway.app/health` → should return `{"status": "ok"}`
9. Add Railway URL to Next.js env as `NEXT_PUBLIC_API_URL`
10. Commit + push: `git commit -m "feat: FastAPI backend + Railway deployment"`

**End of Day Check:** `GET https://[railway-url]/health` returns 200. Railway dashboard shows "Active" status.

---

#### DAY 5 — Document Upload API
**Time: ~5 hours | Deliverable: Can upload a PDF via API and see it in Supabase Storage**

**Morning (2.5 hrs) — Upload Endpoint**
1. Create `apps/api/app/routers/documents.py`:
   ```python
   POST /documents/upload
   - Auth: Depends(verify_jwt)
   - Body: multipart/form-data (file: UploadFile)
   - Validate: file type (pdf/docx/txt/md only), size (< 50MB)
   - Generate: doc_id = uuid4()
   - Upload to Supabase Storage: path = f"orgs/{org_id}/docs/{doc_id}/{filename}"
   - INSERT into documents table (status='pending')
   - Trigger Inngest event: {"name": "doc/uploaded", "data": {"doc_id": str, "org_id": str, "file_path": str}}
   - Return: {"doc_id": str, "status": "pending"}
   
   GET /documents
   - Auth: Depends(verify_jwt)  
   - Return: list of documents for user's org (via RLS user client)
   
   DELETE /documents/{doc_id}
   - Auth: Depends(verify_jwt)
   - Delete from storage + delete document row (cascades to chunks + embeddings)
   ```

2. Add file validation service:
   ```python
   ALLOWED_TYPES = {"application/pdf", "application/vnd.openxmlformats...", "text/plain", "text/markdown"}
   MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
   ```

**Afternoon (2.5 hrs) — Frontend Upload Component**
3. Install in Next.js: `pnpm add react-dropzone`
4. Create `components/documents/upload-button.tsx`:
   - Button that opens file picker
   - Accepts: .pdf, .docx, .txt, .md
   - On select: POST to `/api/documents/upload` (Next.js API route as proxy)
   - Show progress indicator
   - Show success/error toast

5. Create Next.js API proxy route `/app/api/documents/upload/route.ts`:
   - Receives file from browser
   - Gets Supabase session token
   - Forwards to FastAPI with Authorization header
   - This keeps FastAPI URL server-side only

6. Test: Upload a real PDF → check Supabase Storage → check documents table row with status='pending'

**End of Day Check:** Upload a PDF via the component → see it appear in Supabase Storage under `orgs/{id}/docs/` → see database row with `status='pending'`.

---

#### DAY 6 — Document Parser & Chunker
**Time: ~6 hours | Deliverable: Any uploaded PDF/DOCX/TXT can be parsed into text chunks**

**Morning (3 hrs) — Parsers**
1. Create `apps/api/app/services/ingestion/parser.py`:

   **PDF parser (PyMuPDF):**
   ```python
   def parse_pdf(file_bytes: bytes) -> list[dict]:
       doc = fitz.open(stream=file_bytes, filetype="pdf")
       pages = []
       for page_num, page in enumerate(doc):
           text = page.get_text("text").strip()
           if text:
               pages.append({"content": text, "page_number": page_num + 1})
       return pages
   ```

   **DOCX parser (python-docx):**
   ```python
   def parse_docx(file_bytes: bytes) -> list[dict]:
       doc = Document(BytesIO(file_bytes))
       sections = []
       current_heading = None
       for para in doc.paragraphs:
           if para.style.name.startswith('Heading'):
               current_heading = para.text
           elif para.text.strip():
               sections.append({"content": para.text, "section_heading": current_heading, "page_number": None})
       return sections
   ```

   **TXT/MD parser:**
   ```python
   def parse_text(file_bytes: bytes) -> list[dict]:
       text = file_bytes.decode('utf-8', errors='replace')
       return [{"content": text, "page_number": None}]
   ```

   **Router function:**
   ```python
   def parse_document(file_bytes: bytes, file_type: str) -> list[dict]:
       if file_type == 'pdf': return parse_pdf(file_bytes)
       elif file_type == 'docx': return parse_docx(file_bytes)
       else: return parse_text(file_bytes)
   ```

2. Write unit tests for each parser with sample files (create 3 small test files)

**Afternoon (3 hrs) — Chunker**
3. Create `apps/api/app/services/ingestion/chunker.py`:
   ```python
   from langchain_text_splitters import RecursiveCharacterTextSplitter
   
   splitter = RecursiveCharacterTextSplitter(
       chunk_size=512,
       chunk_overlap=64,
       length_function=len,
       separators=["\n\n", "\n", ". ", " ", ""]
   )
   
   def chunk_pages(pages: list[dict], doc_id: str, org_id: str) -> list[dict]:
       chunks = []
       chunk_index = 0
       for page in pages:
           splits = splitter.split_text(page["content"])
           for split in splits:
               chunks.append({
                   "content": split,
                   "chunk_index": chunk_index,
                   "page_number": page.get("page_number"),
                   "section_heading": page.get("section_heading"),
                   "doc_id": doc_id,
                   "org_id": org_id,
               })
               chunk_index += 1
       return chunks
   ```

4. Write unit test: feed a 5-page sample PDF → verify chunks are created with correct metadata
5. Verify chunk sizes are within 512 tokens (approximate with `len(text) / 4`)

**End of Day Check:** `parse_pdf(bytes) → chunk_pages(pages)` produces a list of dicts with content + metadata. Run unit tests: all pass.

---

#### DAY 7 — Embedding Pipeline
**Time: ~5 hours | Deliverable: Chunks are embedded and stored in pgvector**

**Morning (2.5 hrs) — Embedder**
1. Create `apps/api/app/services/ingestion/embedder.py`:
   ```python
   import google.generativeai as genai
   
   genai.configure(api_key=settings.GEMINI_API_KEY)
   
   def embed_chunks(chunks: list[dict]) -> list[dict]:
       texts = [c["content"] for c in chunks]
       # Batch in groups of 100
       embeddings = []
       for i in range(0, len(texts), 100):
           batch = texts[i:i+100]
           result = genai.embed_content(
               model="models/text-embedding-004",
               content=batch,
               task_type="RETRIEVAL_DOCUMENT"
           )
           embeddings.extend(result['embedding'])
       
       for chunk, embedding in zip(chunks, embeddings):
           chunk["embedding"] = embedding
       return chunks
   ```

2. Create `apps/api/app/services/ingestion/store.py`:
   ```python
   def store_chunks(chunks_with_embeddings: list[dict], supabase_client):
       # Batch INSERT chunks
       chunk_rows = [{"id": str(uuid4()), "org_id": c["org_id"], 
                      "document_id": c["doc_id"], "content": c["content"],
                      "chunk_index": c["chunk_index"], "page_number": c["page_number"],
                      "section_heading": c["section_heading"]} 
                     for c in chunks_with_embeddings]
       
       result = supabase_client.table("chunks").insert(chunk_rows).execute()
       chunk_ids = [r["id"] for r in result.data]
       
       # Batch INSERT embeddings
       embedding_rows = [{"chunk_id": cid, "org_id": c["org_id"],
                          "embedding": c["embedding"]}
                         for cid, c in zip(chunk_ids, chunks_with_embeddings)]
       supabase_client.table("embeddings").insert(embedding_rows).execute()
       
       return chunk_ids
   ```

**Afternoon (2.5 hrs) — Integration Test**
3. Write integration test script `test_pipeline.py`:
   - Load a real PDF from disk
   - Parse → chunk → embed → store
   - Query Supabase to verify rows exist
   - Run: `uv run python test_pipeline.py`

4. Check pgvector index: verify IVFFlat index is being used (`EXPLAIN ANALYZE` in SQL Editor)
5. Fix any issues with embedding dimensions (should be 768 for text-embedding-004)

**End of Day Check:** After running test_pipeline.py, Supabase `chunks` table has rows and `embeddings` table has matching rows with non-null vector values. Query count should match expected number of chunks.

---

### WEEK 2 — AI Core (Days 8–14)
*Goal: Full task → context retrieval → output pipeline working end-to-end, no UI yet.*

---

#### DAY 8 — Async Document Processing (Inngest)
**Time: ~5 hours | Deliverable: Upload a doc, Inngest automatically processes it end-to-end**

**Morning (2.5 hrs) — Inngest Function**
1. Install Inngest in FastAPI: `uv add inngest`
2. Create `apps/api/inngest/process_document.py`:
   ```python
   @inngest_client.create_function(
       fn_id="process-document",
       trigger=inngest.TriggerEvent(event="doc/uploaded"),
       retries=3,
   )
   async def process_document(ctx: inngest.Context, step: inngest.Step):
       doc_id = ctx.event.data["doc_id"]
       org_id = ctx.event.data["org_id"]
       file_path = ctx.event.data["file_path"]
       
       # Step 1: Download file from Supabase Storage
       file_bytes = await step.run("download-file", lambda: download_from_storage(file_path))
       
       # Step 2: Parse
       pages = await step.run("parse-document", lambda: parse_document(file_bytes, get_file_type(file_path)))
       
       # Step 3: Chunk
       chunks = await step.run("chunk-document", lambda: chunk_pages(pages, doc_id, org_id))
       
       # Step 4: Embed + Store
       await step.run("embed-and-store", lambda: embed_and_store(chunks))
       
       # Step 5: Update document status
       await step.run("update-status", lambda: update_document_status(doc_id, "ready", len(chunks)))
   ```

3. Mount Inngest serve handler in `main.py`:
   ```python
   from inngest.fast_api import serve
   serve(app, inngest_client, [process_document])
   ```

**Afternoon (2.5 hrs) — Error Handling**
4. Add error handler: if any step fails 3 times, update document status to `"failed"`
5. Add step to update status → `"processing"` before parsing starts
6. Deploy updated FastAPI to Railway: `git push origin main` → Railway auto-deploys
7. Go to Inngest dashboard → connect to Railway URL (`/api/inngest`)
8. Test: Upload a PDF via Postman/curl → watch Inngest dashboard → verify doc status changes: pending → processing → ready

**End of Day Check:** Inngest dashboard shows a completed function run. Documents table row has `status='ready'` and `chunk_count > 0`.

---

#### DAY 9 — Vector Search
**Time: ~4 hours | Deliverable: Can query pgvector and get top-k semantically relevant chunks**

1. Create `apps/api/app/services/retrieval/vector_search.py`:
   ```python
   def vector_search(query: str, org_id: str, k: int = 10) -> list[dict]:
       # Embed the query
       result = genai.embed_content(
           model="models/text-embedding-004",
           content=query,
           task_type="RETRIEVAL_QUERY"  # different task type for queries
       )
       query_embedding = result['embedding']
       
       # Search pgvector
       response = supabase.rpc("vector_search", {
           "query_embedding": query_embedding,
           "match_org_id": org_id,
           "match_count": k
       }).execute()
       
       return response.data
   ```

2. Create Supabase SQL function via migration `003_search_functions.sql`:
   ```sql
   CREATE OR REPLACE FUNCTION vector_search(
     query_embedding vector(768),
     match_org_id uuid,
     match_count int DEFAULT 10
   )
   RETURNS TABLE (
     chunk_id uuid, content text, document_id uuid, page_number int,
     section_heading text, doc_name text, similarity float
   )
   LANGUAGE sql STABLE
   AS $$
     SELECT c.id, c.content, c.document_id, c.page_number, c.section_heading,
            d.name as doc_name,
            1 - (e.embedding <=> query_embedding) AS similarity
     FROM embeddings e
     JOIN chunks c ON c.id = e.chunk_id
     JOIN documents d ON d.id = c.document_id
     WHERE e.org_id = match_org_id
     ORDER BY e.embedding <=> query_embedding
     LIMIT match_count;
   $$;
   ```

3. Push migration: `supabase db push`
4. Test with Python script: ask 5 different questions about a test document, verify results are sensible

**End of Day Check:** `vector_search("what is our vacation policy", org_id)` returns 10 chunks ranked by relevance.

---

#### DAY 10 — Full-Text Search
**Time: ~3 hours | Deliverable: Keyword search works alongside vector search**

1. Create `apps/api/app/services/retrieval/fts_search.py`:
   ```python
   def fts_search(query: str, org_id: str, k: int = 10) -> list[dict]:
       response = supabase.rpc("fts_search", {
           "query_text": query,
           "match_org_id": org_id,
           "match_count": k
       }).execute()
       return response.data
   ```

2. Add SQL function to `003_search_functions.sql`:
   ```sql
   CREATE OR REPLACE FUNCTION fts_search(
     query_text text,
     match_org_id uuid,
     match_count int DEFAULT 10
   )
   RETURNS TABLE (chunk_id uuid, content text, document_id uuid, 
                  page_number int, section_heading text, doc_name text, rank float)
   LANGUAGE sql STABLE
   AS $$
     SELECT c.id, c.content, c.document_id, c.page_number, c.section_heading,
            d.name,
            ts_rank_cd(c.content_tsv, to_tsquery('english', 
              regexp_replace($1, '\s+', ' & ', 'g'))) AS rank
     FROM chunks c
     JOIN documents d ON d.id = c.document_id
     WHERE c.org_id = match_org_id
       AND c.content_tsv @@ to_tsquery('english',
             regexp_replace($1, '\s+', ' & ', 'g'))
     ORDER BY rank DESC
     LIMIT match_count;
   $$;
   ```

3. Test: search for an exact company name or product code from test document. Verify it shows up even if semantically distant.

**End of Day Check:** `fts_search("Q4 revenue target 2024", org_id)` finds the exact chunk containing that phrase. Vector search alone might miss it.

---

#### DAY 11 — Hybrid Search (RRF Fusion)
**Time: ~4 hours | Deliverable: Single search function that beats both vector and FTS alone**

1. Create `apps/api/app/services/retrieval/hybrid_search.py`:
   ```python
   def reciprocal_rank_fusion(
       vector_results: list[dict],
       fts_results: list[dict],
       k: int = 60  # RRF constant
   ) -> list[dict]:
       scores = {}
       
       # Score from vector search
       for rank, chunk in enumerate(vector_results):
           cid = chunk["chunk_id"]
           scores[cid] = scores.get(cid, {"chunk": chunk, "score": 0})
           scores[cid]["score"] += 1 / (k + rank + 1)
       
       # Score from FTS
       for rank, chunk in enumerate(fts_results):
           cid = chunk["chunk_id"]
           if cid not in scores:
               scores[cid] = {"chunk": chunk, "score": 0}
           scores[cid]["score"] += 1 / (k + rank + 1)
       
       # Sort by combined score, return top results
       ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
       return [item["chunk"] for item in ranked[:10]]
   
   
   def hybrid_search(query: str, org_id: str) -> list[dict]:
       vector_results = vector_search(query, org_id, k=10)
       fts_results = fts_search(query, org_id, k=10)
       return reciprocal_rank_fusion(vector_results, fts_results)
   ```

2. Test with 10 varied questions against your test documents. Compare results of:
   - Vector only
   - FTS only
   - Hybrid
   
   Hybrid should win or tie on all questions.

**End of Day Check:** `hybrid_search()` consistently returns better or equal results compared to either method alone. Top result is almost always the right one.

---

#### DAY 12 — LLM + Tool-Use Setup
**Time: ~5 hours | Deliverable: LLM can call the search tool, we execute it, it produces output**

1. Create `apps/api/app/services/llm/client.py`:
   ```python
   import google.generativeai as genai
   from google.generativeai.types import FunctionDeclaration, Tool
   
   SYSTEM_PROMPT = """You are Nirnaya IQ, an AI assistant that helps employees 
   of this company execute work tasks using the company's internal knowledge.

   You have access to a tool: search_company_knowledge(query).
   Use it to retrieve relevant context BEFORE completing any task.

   Rules:
   - Always call search_company_knowledge at least once before generating output.
   - Call it multiple times with different targeted queries to gather all context you need.
     Example for "write an email about refunds": search "refund policy", then "customer email tone", then "complaint handling".
   - Use ONLY information retrieved from the tool. Never invent company policies, names, or figures.
   - Match the company's voice and tone as found in the retrieved documents.
   - If you cannot find enough context, say what information is missing and suggest which documents to upload.
   - Output should be ready-to-use: a real email, a real job description, a real Slack message — not a template.
   """
   
   # Define the tool the LLM can call
   SEARCH_TOOL = Tool(function_declarations=[
       FunctionDeclaration(
           name="search_company_knowledge",
           description="Search the company knowledge base. Call with a specific, targeted query. Call multiple times for different aspects of the task.",
           parameters={
               "type": "object",
               "properties": {
                   "query": {
                       "type": "string",
                       "description": "A specific search query, e.g. 'refund policy', 'brand voice guidelines', 'engineering hiring criteria'"
                   }
               },
               "required": ["query"]
           }
       )
   ])
   
   def create_llm_client():
       genai.configure(api_key=settings.GEMINI_API_KEY)
       return genai.GenerativeModel(
           model_name="gemini-2.0-flash",
           system_instruction=SYSTEM_PROMPT,
           tools=[SEARCH_TOOL]
       )
   ```

2. Create context formatter (same as before — formats retrieved chunks for LLM):
   ```python
   def format_context(chunks: list[dict]) -> str:
       context_parts = []
       for i, chunk in enumerate(chunks):
           source = chunk['doc_name'] + (f", Page {chunk['page_number']}" if chunk['page_number'] else "")
           context_parts.append(f"[SOURCE: {source}]\n{chunk['content']}")
       return "\n\n---\n\n".join(context_parts)
   ```

3. Test the tool definition with Gemini:
   ```python
   model = create_llm_client()
   chat = model.start_chat()
   response = chat.send_message("Write a job description for a senior engineer")
   # Verify response.parts[0].function_call exists
   # Verify function_call.name == "search_company_knowledge"
   # Verify function_call.args has a "query" key
   print(response.parts[0].function_call)
   ```
4. Verify Gemini makes at least 2 tool calls for a complex task

**End of Day Check:** Sending "Write an email to a client about our pricing" to Gemini returns a `function_call` response (not text). The query in the function call is relevant (e.g. "pricing policy" not the full task description).

---

#### DAY 13 — Full Tool-Use Chain
**Time: ~6 hours | Deliverable: POST /chat executes multi-search tool-use and returns grounded output**

1. Create `apps/api/app/services/llm/task_chain.py`:
   ```python
   import asyncio
   
   async def execute_task(
       user_message: str,
       org_id: str,
       conversation_history: list[dict] = None
   ) -> dict:
       model = create_llm_client()
       history = (conversation_history or [])[-6:]
       chat = model.start_chat(history=format_history(history))
       
       # Step 1: Send user task to LLM — it will respond with tool calls
       response = chat.send_message(user_message)
       
       all_sources = []
       
       # Step 2: Execute tool calls in parallel (LLM may request 2–4 searches)
       while response.parts and any(p.function_call for p in response.parts):
           tool_calls = [p.function_call for p in response.parts if p.function_call]
           
           # Run all searches in parallel
           search_results = await asyncio.gather(*[
               run_search(tc.args["query"], org_id)
               for tc in tool_calls
           ])
           
           # Collect all unique sources
           for results in search_results:
               all_sources.extend(results)
           
           # Step 3: Return tool results back to LLM
           tool_responses = [
               genai.protos.Part(function_response=genai.protos.FunctionResponse(
                   name=tc.name,
                   response={"result": format_context(results)}
               ))
               for tc, results in zip(tool_calls, search_results)
           ]
           response = chat.send_message(tool_responses)
       
       # Step 4: LLM has all context, final response is the generated output
       output = response.text
       
       # Deduplicate sources
       seen = set()
       unique_sources = []
       for s in all_sources:
           key = (s["doc_name"], s.get("page_number"))
           if key not in seen:
               seen.add(key)
               unique_sources.append({"doc_name": s["doc_name"], 
                                      "page_number": s["page_number"],
                                      "excerpt": s["content"][:200]})
       
       return {"output": output, "sources": unique_sources[:6]}
   
   
   async def run_search(query: str, org_id: str) -> list[dict]:
       return hybrid_search(query, org_id)
   ```

2. Create `apps/api/app/routers/chat.py`:
   ```
   POST /chat
   Body: { conversation_id: str (optional), message: str }
   Auth: Depends(verify_jwt)
   - Load conversation history from DB (last 6 messages)
   - Call execute_task()
   - Save user message + assistant message (with sources) to DB
   - Return: { message_id, output, sources, conversation_id }
   ```

3. Test with 5 real task types via curl:
   ```bash
   # Test 1: email writing
   curl -X POST https://[railway-url]/chat \
     -H "Authorization: Bearer [jwt]" \
     -d '{"message": "Write an email to a client asking about our refund policy"}'
   
   # Test 2: job description
   curl ... -d '{"message": "Write a job description for a senior backend engineer"}'
   
   # Test 3: Q&A (still works)
   curl ... -d '{"message": "What is our vacation policy?"}'
   ```

4. Verify for each test:
   - `sources` array is non-empty (real company docs were used)
   - Output quality: does the email actually sound like the company?
   - LLM made multiple tool calls (check logs)

**End of Day Check:** All 3 curl tests return grounded, high-quality output. Sources list shows which documents were used. Email test output is a complete, ready-to-send email — not a template.

---

#### DAY 14 — Streaming Responses
**Time: ~5 hours | Deliverable: Output streams token by token; tool calls shown as progress steps**

The UX challenge with tool-use: there's a pause while the LLM makes tool calls and we run searches. We need to stream progress events so the UI doesn't look frozen.

1. Create `apps/api/app/services/llm/task_chain_stream.py`:
   ```python
   async def execute_task_stream(user_message: str, org_id: str, history: list):
       model = create_llm_client()
       chat = model.start_chat(history=format_history(history))
       
       all_sources = []
       response = chat.send_message(user_message)
       
       # Phase 1: Tool call loop — emit progress events as we search
       while response.parts and any(p.function_call for p in response.parts):
           tool_calls = [p.function_call for p in response.parts if p.function_call]
           
           for tc in tool_calls:
               query = tc.args["query"]
               # Tell the frontend we're searching
               yield f"data: {json.dumps({'type': 'searching', 'query': query})}\n\n"
           
           # Run all searches in parallel
           search_results = await asyncio.gather(*[
               run_search(tc.args["query"], org_id) for tc in tool_calls
           ])
           
           for results in search_results:
               all_sources.extend(results)
           
           tool_responses = [
               genai.protos.Part(function_response=genai.protos.FunctionResponse(
                   name=tc.name,
                   response={"result": format_context(results)}
               ))
               for tc, results in zip(tool_calls, search_results)
           ]
           response = chat.send_message(tool_responses)
       
       # Phase 2: Emit deduplicated sources
       unique_sources = deduplicate_sources(all_sources)
       yield f"data: {json.dumps({'type': 'sources', 'sources': unique_sources})}\n\n"
       
       # Phase 3: Stream final output tokens
       # Note: Gemini doesn't stream after tool-use in the same call.
       # Send a final streaming generation call with the gathered context.
       final_context = format_context(all_sources[:20])
       stream_prompt = f"Context retrieved:\n\n{final_context}\n\nNow complete the task: {user_message}"
       
       streaming_model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=SYSTEM_PROMPT)
       stream = streaming_model.generate_content(stream_prompt, stream=True)
       
       full_text = ""
       for chunk in stream:
           token = chunk.text
           full_text += token
           yield f"data: {json.dumps({'type': 'token', 'token': token})}\n\n"
       
       save_messages_to_db(user_message, full_text, unique_sources)
       yield f"data: {json.dumps({'type': 'done'})}\n\n"
   ```

2. Update chat router:
   ```python
   @router.post("/chat/stream")
   async def chat_stream(body: ChatRequest, user=Depends(verify_jwt)):
       history = load_conversation_history(body.conversation_id)
       return StreamingResponse(
           execute_task_stream(body.message, user["org_id"], history),
           media_type="text/event-stream"
       )
   ```

3. SSE event types the frontend must handle:
   ```
   { type: "searching", query: "refund policy" }     ← show "Searching: refund policy..."
   { type: "searching", query: "customer tone" }      ← show "Searching: customer tone..."
   { type: "sources", sources: [...] }                ← populate sources panel
   { type: "token", token: "Dear " }                 ← stream into output box
   { type: "done" }                                   ← mark complete
   ```

4. Test: Send a complex task, watch terminal output — should see searching events before tokens start.

**End of Day Check:** 3 event types appear in order: searching → sources → token stream → done. No silent pause in the terminal output.

---

### WEEK 3 — Frontend (Days 15–21)
*Goal: A complete, working UI that a real user could use.*

---

#### DAY 15 — Next.js UI Foundation
**Time: ~5 hours | Deliverable: shadcn/ui installed, design tokens set, components work**

1. Install shadcn/ui:
   ```bash
   cd apps/web
   pnpm dlx shadcn@latest init
   # Choose: Default style, Slate base color, CSS variables
   ```

2. Install core components:
   ```bash
   pnpm dlx shadcn@latest add button input card dialog toast badge 
   pnpm dlx shadcn@latest add dropdown-menu avatar separator scroll-area
   pnpm dlx shadcn@latest add skeleton progress alert
   ```

3. Install utilities: `pnpm add next-themes clsx tailwind-merge lucide-react`

4. Update `app/layout.tsx`:
   - Add ThemeProvider (next-themes)
   - Add Toaster (shadcn)
   - Set font (Inter via next/font)
   - Meta: title "Nirnaya IQ", description

5. Set color tokens in `globals.css` — company brand: deep blue + white
6. Create `lib/utils.ts` with `cn()` helper (combine clsx + tailwind-merge)
7. Verify: all shadcn components render without errors on a test page

**End of Day Check:** `pnpm dev` runs, `localhost:3000` renders with your color theme. A test page using Card, Button, Badge renders correctly.

---

#### DAY 16 — Auth Pages
**Time: ~5 hours | Deliverable: Users can sign up, log in, log out**

1. `app/(auth)/login/page.tsx`:
   - Logo + "Sign in to Nirnaya IQ"
   - Email field, password field
   - Submit button (shows loading spinner)
   - Error alert for wrong credentials
   - Link: "Don't have an account? Sign up"

2. `app/(auth)/signup/page.tsx`:
   - "Create your Nirnaya IQ"
   - Fields: Full name, Company name, Email, Password, Confirm password
   - Password strength indicator
   - Submit button + loading state
   - Error handling
   - Link: "Already have an account? Log in"

3. Create auth hook `hooks/use-auth.ts`:
   - `useUser()` — returns current user + org
   - `useSignOut()` — calls `supabase.auth.signOut()` + redirects to /login

4. Test the full flow:
   - Sign up with a new email → verify redirect to /dashboard
   - Sign out → verify redirect to /login
   - Log back in → verify redirect to /dashboard
   - Manually visit /dashboard when logged out → verify redirect to /login

**End of Day Check:** Complete auth cycle works without errors. Auth state persists on page refresh.

---

#### DAY 17 — Dashboard Layout
**Time: ~4 hours | Deliverable: App shell with sidebar navigation**

1. Create `app/(dashboard)/layout.tsx`:
   - Sidebar (240px, fixed):
     - Logo + app name at top
     - Nav links: Chat (MessageSquare icon), Documents (FileText), Settings (Settings)
     - Active link highlighted
     - User avatar + org name at bottom + sign out button
   - Main content area: flex-1, overflow-y-auto

2. Create `components/layout/sidebar.tsx`:
   - Use `usePathname()` from next/navigation for active state
   - Nav items as array: `[{href, label, icon}]`
   - Responsive: hide sidebar on mobile (hamburger menu, skip for now)

3. Create placeholder pages:
   - `app/(dashboard)/chat/page.tsx` → "Chat coming soon"
   - `app/(dashboard)/documents/page.tsx` → "Documents coming soon"
   - `app/(dashboard)/settings/page.tsx` → "Settings coming soon"

4. Test: all nav links work, active state updates correctly

**End of Day Check:** Sidebar renders with all nav items. Clicking nav items updates the URL and active state.

---

#### DAY 18 — Documents Page
**Time: ~6 hours | Deliverable: Users can upload, view, and delete documents**

1. Create API client in Next.js `lib/api-client.ts`:
   ```typescript
   // Thin wrapper that attaches Supabase Bearer token to all FastAPI requests
   export async function apiClient(path: string, options?: RequestInit) {
     const { data: { session } } = await supabase.auth.getSession()
     return fetch(`${process.env.NEXT_PUBLIC_API_URL}${path}`, {
       ...options,
       headers: {
         'Authorization': `Bearer ${session?.access_token}`,
         ...options?.headers
       }
     })
   }
   ```

2. Create `hooks/use-documents.ts`:
   - `useDocuments()` — fetches document list with SWR/React Query
   - Refetches when a document status changes

3. Create `app/(dashboard)/documents/page.tsx`:
   - Header: "Documents" + "Upload Document" button
   - Document table: columns = Name, Type, Status, Chunks, Date, Actions
   - Status badge: pending (gray), processing (blue spinner), ready (green), failed (red)
   - Delete button (confirm dialog)
   - Empty state: "No documents yet. Upload your first document to get started."

4. Create upload dialog `components/documents/upload-dialog.tsx`:
   - DropZone (react-dropzone) for drag-and-drop
   - Shows selected file name + size
   - Upload button
   - Progress bar during upload
   - Closes on success, shows toast "Document uploaded, processing..."

5. Install: `pnpm add react-dropzone swr`

**End of Day Check:** Documents page shows list. Click "Upload" → drag PDF → click upload → see new row appear with status "pending". Status changes to "processing" after a few seconds (requires Day 21 Realtime).

---

#### DAY 19 — Task Interface (Chat)
**Time: ~7 hours | Deliverable: Users can describe any work task and receive grounded, ready-to-use output**

The UI has three zones: input (describe the task), output (generated content), sources (what docs were used).

1. Create `app/(dashboard)/chat/page.tsx` layout:
   - Left panel (260px): Conversation history list + "New Task" button
   - Right panel: Two-zone layout
     - **Output area** (top, ~70%): rendered assistant output + user messages
     - **Input bar** (bottom, sticky): large textarea + send button

2. Create `components/chat/message-list.tsx`:
   - User messages: right-aligned, muted background, smaller text
   - Assistant output: left-aligned, white card, full width — this is the main content
   - Each assistant message has a **"Searching..."** state that shows tool call progress:
     ```
     🔍 Searching: "refund policy"
     🔍 Searching: "customer email tone"
     ✓ Found context in 3 documents
     ```
   - Once done: renders the final output (email / JD / answer)
   - Auto-scrolls to bottom

3. Create `components/chat/searching-indicator.tsx`:
   - Shows during tool-use phase (between send and first token)
   - Lists each search query as it arrives via SSE `type: searching` event
   - Gives user confidence something is happening (not a frozen spinner)

4. Create `components/chat/message-input.tsx`:
   - Textarea (auto-grows, max 6 lines)
   - Placeholder: *"Ask anything or describe a task — write an email, draft a job description, summarize a policy..."*
   - Send button (Paper Plane icon)
   - Enter sends, Shift+Enter = new line
   - Disabled during generation

5. Create `hooks/use-chat.ts`:
   - `sendMessage(text)` → POST to `/chat/stream` → reads SSE stream
   - On `type: searching` → add to searching state (shows in indicator)
   - On `type: sources` → store sources on current message
   - On `type: token` → append token to output text (streaming effect)
   - On `type: done` → mark complete, clear searching state

6. Suggested task prompts (shown in empty state):
   ```
   ✉️  "Write an email to a new client introducing our services"
   💼  "Create a job description for a senior designer"
   📣  "Draft a Slack message announcing our new leave policy"
   ❓  "What is our process for handling customer complaints?"
   📋  "Summarize everything a new hire needs to know in week one"
   ```

7. Add "No documents" warning banner when doc count = 0:
   "The brain has no knowledge yet. Upload your company documents to get started."

**End of Day Check:** Type "Write an email to a client asking about our refund process" → see searching indicators appear → sources card populates → email streams in token by token → complete email is ready to copy. Conversation persists on refresh.

---

#### DAY 20 — Citations Component
**Time: ~5 hours | Deliverable: Each answer shows clickable source cards**

1. Create `components/chat/citations.tsx`:
   ```tsx
   // Shows below each assistant message
   // Renders array of sources as small cards:
   //   [📄 Employee Handbook, Page 3]  [📄 Q4 Report]
   // Click expands to show excerpt
   ```

2. Citation card component:
   - Document icon + doc name + page number (if available)
   - Click → expand to show 200-char excerpt
   - Optional: "View full document" link (opens Supabase Storage signed URL in new tab)

3. Create API route `app/api/documents/[id]/signed-url/route.ts`:
   - Gets Supabase Storage signed URL (valid 1 hour)
   - Returns URL to frontend

4. Update message rendering: if message has `sources` array, render Citations below the message text

5. Add "verified by X sources" badge on messages with citations

**End of Day Check:** Ask a question → answer appears → below answer see 2-3 citation cards → click card → see excerpt. Click "View document" → PDF opens in new tab.

---

#### DAY 21 — Real-time Document Status
**Time: ~4 hours | Deliverable: Document status updates automatically without page refresh**

1. Create `hooks/use-document-realtime.ts`:
   ```typescript
   // Subscribes to Supabase Realtime changes on 'documents' table
   // Filtered to current user's org_id
   // On UPDATE event: update document in local state (SWR mutate)
   useEffect(() => {
     const channel = supabase
       .channel('document-changes')
       .on('postgres_changes', {
         event: 'UPDATE',
         schema: 'public',
         table: 'documents',
         filter: `org_id=eq.${orgId}`
       }, (payload) => {
         mutate() // refresh document list
         if (payload.new.status === 'ready') {
           toast.success(`"${payload.new.name}" is ready!`)
         }
       })
       .subscribe()
     return () => supabase.removeChannel(channel)
   }, [orgId])
   ```

2. Enable Realtime for `documents` table in Supabase Dashboard:
   - Database → Replication → enable `documents` table

3. Add this hook to the Documents page

4. Test: Upload a document → watch status badge update live: pending → processing → ready → toast appears

**End of Day Check:** Upload a doc, don't refresh the page, watch the status badge update live from pending to ready. Toast notification fires.

---

### WEEK 4 — Polish & Launch (Days 22–30)
*Goal: Production-ready, secure, shipped.*

---

#### DAY 22 — Settings Page
**Time: ~4 hours | Deliverable: Users can update org name and their display name**

1. `app/(dashboard)/settings/page.tsx` with three sections:
   **Workspace Settings:**
   - Org name (editable)
   - Org slug (read-only, shown for reference)
   - Save button

   **Your Account:**
   - Display name (editable)
   - Email (read-only)
   - Save button

   **Danger Zone:**
   - "Delete my account" button → confirmation dialog → `supabase.auth.admin.deleteUser()`

2. Create API routes for settings updates (Next.js API routes that update Supabase via service role)

3. Add loading + success/error toasts on save

**End of Day Check:** Change org name → save → reload page → org name persists. Sidebar shows updated name.

---

#### DAY 23 — Error Handling & Loading States
**Time: ~5 hours | Deliverable: App never shows a blank screen or silent failure**

1. Skeleton loaders for:
   - Document list (3 skeleton rows while loading)
   - Chat message list (2 skeleton messages)
   - Sidebar (skeleton user + nav items)

2. Error boundaries:
   - Wrap all pages in `ErrorBoundary` component
   - Shows "Something went wrong. Try refreshing." with retry button

3. Toast error handling: every `apiClient()` call that fails shows a descriptive toast

4. Empty states with CTAs:
   - No conversations: "Ask your first question" + example questions
   - No documents: "Upload your first document" + upload button
   - Failed document: "Processing failed. Try uploading again." + retry button

5. Network offline handling: show banner "You're offline" when browser loses connection

**End of Day Check:** Disconnect internet → see offline banner. Delete a document → confirm dialog appears. Upload fails → toast shows error message.

---

#### DAY 24 — End-to-End QA
**Time: ~8 hours | Deliverable: Zero critical bugs in the full user journey**

**Run these exact test scenarios:**
1. **New user signup → first document → first question:**
   - Sign up → upload 2 PDFs (different types) → wait for processing → ask 5 questions → verify answers have citations

2. **Multi-tenant isolation test:**
   - Create org A + org B (two browser sessions)
   - Upload different docs to each
   - Ask same question → verify different answers (no cross-org data)

3. **Edge cases:**
   - Upload a 10MB PDF with many pages
   - Ask a question when no docs uploaded
   - Ask a very long question (500+ characters)
   - Ask the same question twice in a conversation (history test)
   - Delete a document while it's being processed

4. Fix every bug found. Prioritize: data correctness > UI > performance.

**End of Day Check:** All 3 scenarios pass without crashes. Multi-tenant test shows zero cross-org data leak.

---

#### DAY 25 — Performance & Caching
**Time: ~4 hours | Deliverable: Pages load fast, RAG queries cache for 5 minutes**

1. Add query result caching in FastAPI (Upstash Redis):
   ```python
   # Cache hybrid_search results for 5 minutes per (org_id, query_hash)
   cache_key = f"search:{org_id}:{hashlib.md5(query.encode()).hexdigest()}"
   cached = redis_client.get(cache_key)
   if cached:
       return json.loads(cached)
   # ... run search ...
   redis_client.setex(cache_key, 300, json.dumps(results))
   ```

2. Add missing DB indexes (check with `EXPLAIN ANALYZE` on slow queries):
   - `documents(org_id, created_at DESC)` for document list
   - `messages(conversation_id, created_at)` for chat history
   - `conversations(user_id, updated_at DESC)` for conversation list

3. Next.js: enable `next/image` optimization for any images
4. Lazy load conversation history (paginate: load 20 messages, "load more" button)
5. Measure: time how long a typical RAG query takes. Target < 3s total.

**End of Day Check:** Second identical query returns cached result in < 100ms. Document list page loads < 500ms.

---

#### DAY 26 — Landing Page
**Time: ~6 hours | Deliverable: A real marketing page that explains the product**

1. Create `app/(marketing)/page.tsx` (root `/` route):

   **Hero section:**
   - H1: "Your Company's Brain. Always Available."
   - Subheadline: "Stop digging through Google Drive. Ask a question, get an answer with sources in seconds."
   - CTA button: "Start for Free" → /signup
   - Optional: screenshot/mockup of the chat interface

   **Pain points section:**
   - 3 columns: "Hours lost searching documents" / "Tribal knowledge walks out the door" / "New hires take months to get up to speed"

   **How it works (3 steps):**
   1. Upload your documents (PDFs, Word docs, SOPs)
   2. Ask questions in plain English
   3. Get answers with source citations

   **Pricing section:**
   - 3 plan cards: Starter ($49), Team ($129), Business ($299)
   - "Most Popular" badge on Team
   - Monthly/Annual toggle (show savings)
   - CTA on each card

   **FAQ:**
   - What document types are supported?
   - How is my data kept private?
   - Can I cancel anytime?

2. Navigation: Logo, "Pricing" link, "Log in" link, "Start Free" button

**End of Day Check:** Landing page renders correctly. All CTAs link to signup. Pricing section shows all 3 plans.

---

#### DAY 27 — Onboarding Flow
**Time: ~5 hours | Deliverable: First-time users know exactly what to do next**

1. Create onboarding checklist stored in DB (per org):
   - `onboarding_steps: { upload_first_doc: bool, ask_first_question: bool }`

2. Show checklist banner on dashboard for new orgs:
   ```
   ✅ Create your account
   ⬜ Upload your first document  → [Upload Now]
   ⬜ Ask your first question     → [Go to Chat]
   ```

3. After uploading first doc: auto-navigate to chat, pre-fill with suggestion:
   "Your document is ready! Try asking: 'Give me a summary of [doc name]'"

4. Sample question suggestions in empty chat:
   - "What is our return policy?"
   - "Summarize the employee handbook"
   - "What are the steps for onboarding a new client?"

5. Mark onboarding complete → hide banner → show "You're all set!" toast

**End of Day Check:** New user sees checklist. Upload doc → chat opens with suggestion. Ask question → checklist completes.

---

#### DAY 28 — Security Review
**Time: ~4 hours | Deliverable: No security holes before going to real customers**

**Run each test manually:**

1. **RLS verification:**
   ```sql
   -- In Supabase SQL Editor, SET ROLE to a user from Org A
   -- SELECT * FROM documents WHERE org_id = [Org B's ID];
   -- Should return 0 rows
   SET LOCAL role authenticated;
   SET LOCAL request.jwt.claims = '{"sub": "[org-a-user-id]", "org_id": "[org-a-id]"}';
   SELECT count(*) FROM documents; -- must return only org A's docs
   ```

2. **File upload security:**
   - Try uploading a `.exe` file → must be rejected with 400
   - Try uploading a file with MIME type spoofed (Content-Type: text/plain but file is PHP) → validate by reading file magic bytes

3. **Auth bypass test:**
   - Call `GET /documents` with no Authorization header → must get 401
   - Call with a tampered JWT → must get 401
   - Call with a valid JWT from different org → RLS blocks data

4. **Input validation:**
   - Send a 10,000-character chat message → must be truncated/rejected
   - Send empty message → must get 400
   - Send message with SQL injection attempt → verify it's treated as plain text

5. **Fix every issue found immediately.**

**End of Day Check:** All 4 security tests pass. No unauthorized data access possible.

---

#### DAY 29 — Production Deployment
**Time: ~4 hours | Deliverable: App is live on a real URL, monitored**

1. **Vercel production setup:**
   - Deploy from GitHub `main` branch
   - Add all env vars to Vercel project settings
   - Set `NEXT_PUBLIC_API_URL` to Railway production URL
   - Verify build succeeds

2. **Railway production verification:**
   - Check all env vars are set
   - Verify `GET /health` returns 200 on production URL
   - Check Railway metrics: CPU + memory usage nominal

3. **Supabase production check:**
   - Confirm pgvector extension enabled
   - Confirm Realtime enabled for `documents` table
   - Confirm RLS enabled on all tables
   - Confirm storage bucket exists and has correct policies

4. **Custom domain (if you own one):**
   - Add domain in Vercel → update DNS records → wait for propagation

5. **Uptime monitoring (free):**
   - Sign up at uptimerobot.com
   - Add monitor: `https://your-production-url.com/api/health` → check every 5 minutes
   - Add email alert for downtime

6. **Smoke test production:**
   - Sign up as new user on production URL
   - Upload a test PDF
   - Ask 3 questions
   - Verify citations work

**End of Day Check:** Production URL is live. Uptime Robot shows green. Full user journey works on production.

---

#### DAY 30 — Soft Launch
**Time: ~4 hours | Deliverable: First 5 potential customers have been contacted**

1. **Create demo workspace:**
   - Create an org called "Demo Company"
   - Upload 3-4 realistic sample documents (company handbook, FAQ, process doc)
   - Prepare 5 good demo questions with impressive answers
   - Note the demo credentials

2. **Record a 2-minute Loom demo:**
   - Screen record: landing page → sign up → upload doc → ask question → show citations
   - Narrate: "This is how Nirnaya IQ works. You upload your documents, then any employee can get instant answers."
   - Upload to loom.com (free)

3. **Identify your first 5 targets:**
   - LinkedIn: search for "COO" or "Operations Manager" at 20–200 person companies
   - OR post in communities: IndieHackers, Reddit r/entrepreneur, Product Hunt "upcoming"
   - OR your personal network — who runs a small company you know?

4. **Outreach message template:**
   ```
   Hi [Name],
   
   I built something that might save your team hours every week.
   
   Nirnaya IQ lets employees ask plain-English questions and get 
   instant answers from your internal docs — SOPs, handbooks, meeting 
   notes — with source citations so they can verify the answer.
   
   2-min demo: [loom link]
   
   Would you be open to a 15-minute call to see if it fits [Company]?
   
   — Aniket
   ```

5. **Setup Notion (or linear.app) for customer feedback:**
   - Page: "Customer Feedback"
   - Log every piece of feedback, question, objection
   - This is your product roadmap source of truth

6. **Tag v1.0.0:**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

**End of Day Check:** Loom video recorded. 5 messages sent. GitHub tag pushed. You built a production SaaS in 30 days.

---

## Summary Timeline

```
Week 1  (Days 1–7):  Infrastructure — repo, DB, auth, upload, parse, chunk, embed
Week 2  (Days 8–14): Intelligence — async pipeline, vector search, FTS, hybrid, LLM, streaming
Week 3  (Days 15–21): Interface — UI foundation, auth pages, dashboard, docs, chat, realtime
Week 4  (Days 22–30): Launch — polish, QA, performance, landing page, security, deploy, outreach
```

---

*Generated: 2026-06-06 | Phase: v1 MVP*
