# Smoke Testing Day 10 + 11 (Hybrid Search)

## Prerequisites

1. **Database migrations applied:**
   ```bash
   cd supabase
   supabase db push
   ```
   This applies `005_fts_and_hybrid.sql`, which adds:
   - `fts_search(query_text, match_org_id, match_count, match_document_id)` RPC
   - Uses `websearch_to_tsquery` for robust parsing
   - Returns `ts_headline` snippets for citation UI

2. **Test data set up:**
   - You need an **organization** with at least one **document** that's been **processed** (status='ready', chunk_count > 0)
   - If you don't have this yet:
     - Sign up at `http://localhost:3000` to create an org
     - Upload a PDF (Day 5)
     - Wait for Inngest to process it (Day 8) — status should change from `pending` → `processing` → `ready`

3. **Get your org_id:**
   ```bash
   # In Supabase Dashboard → SQL Editor, run:
   SELECT id, name FROM organizations LIMIT 1;
   # Copy the `id` value
   ```

## Running the Smoke Test

### Option 1: Compare all three modes side-by-side (recommended)

```bash
cd apps/api

# On Windows:
uv run python scripts/smoke_hybrid_search.py --org-id <your-org-id> --query "your question here" --compare

# On Mac/Linux:
uv run python scripts/smoke_hybrid_search.py --org-id <your-org-id> --query "your question here" --compare
```

Example:
```bash
uv run python scripts/smoke_hybrid_search.py --org-id 550e8400-e29b-41d4-a716-446655440000 --query "What is our vacation policy?" --compare
```

**Output shows:**
- **VECTOR**: Semantic similarity matches (e.g., "paid time off" ≈ "vacation policy")
- **FTS**: Exact keyword matches (e.g., "Q4-2024", proper nouns)
- **HYBRID**: RRF fusion of both (should be the best overall)

Each hit shows:
- Similarity score
- Document name, page number, section
- Which branch(es) contributed (`via=vector`, `via=fts`, `via=vector+fts`)
- Snippet (highlighted match from FTS, or excerpt from content)

### Option 2: Test individual modes

```bash
# Vector only
uv run python scripts/smoke_hybrid_search.py --org-id <id> --query "..." --mode vector

# FTS only
uv run python scripts/smoke_hybrid_search.py --org-id <id> --query "..." --mode fts

# Hybrid only (default)
uv run python scripts/smoke_hybrid_search.py --org-id <id> --query "..."
```

### Option 3: Test with document scope filter

```bash
# If you want to search within a specific document:
uv run python scripts/smoke_hybrid_search.py --org-id <id> --query "..." --doc <doc-id> --compare
```

## What to Look For

### Good signs:
✅ All three modes return results without errors  
✅ HYBRID results look better than either branch alone  
✅ Chunks appearing in both branches bubble to top (RRF working)  
✅ Rare terms/codes are caught by FTS that vector might miss  
✅ Semantic variations are caught by vector  
✅ Snippets show highlighted keywords from FTS  

### Red flags:
❌ FTS returns no results on a normal question (check if `websearch_to_tsquery` is choking)  
❌ Vector always ranks higher than HYBRID (RRF may have a bug)  
❌ Identical results from vector and FTS (one branch not running)  

## Endpoint Testing (Optional)

After smoke-testing, you can also hit the debug endpoint directly:

```bash
# Get a Supabase auth token (from the Next.js frontend, copy from Network tab)
export TOKEN="<your-jwt>"

# Test vector mode
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/search?q=vacation+policy&mode=vector"

# Test FTS mode
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/search?q=Q4+2024&mode=fts"

# Test hybrid (default)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/search?q=vacation+policy"
```

## Troubleshooting

**"Migration 005 not found" error:**
- Run `supabase db push` from the `supabase/` directory

**"No orgs found":**
- Sign up at http://localhost:3000 first

**"No chunks found":**
- Upload a document, wait for Inngest to process it (check the documents page for status)
- Verify it reached status='ready' before smoke testing

**FTS returns empty results:**
- This can happen if the query is only stopwords (e.g., "the a an")
- Try: "Q4 2024" or a specific product/person name
- Check Postgres logs: `websearch_to_tsquery` might be raising

**Vector returns empty results:**
- Check if the embeddings table has rows: `SELECT COUNT(*) FROM embeddings WHERE org_id = '<your-org-id>';`
- If zero, the pipeline didn't complete

## Next Steps

Once smoke testing looks good, you're ready for:
- **Day 12**: Wire hybrid_search into the LLM's `search_company_knowledge` tool
- **Day 13**: Full chat endpoint with tool-use loop
- **Day 14**: Streaming responses

The `HybridRetriever` is production-ready and will be the backbone of the AI's retrieval layer.
