-- ── Day 10–12: Chat scope, conversation summary, per-chunk embedding state,
-- ── citation analytics. One migration so a single `supabase db push` brings
-- ── the schema fully in line with the feature batch.

-- ── 1. Conversations: scope + summarization (Day 10 #100, Day 12 #53) ───────
-- `scoped_document_id` pins a conversation to a single document — every
-- `search_company_knowledge` call from the LLM will filter retrieval to that
-- doc. NULL = normal org-wide chat. ON DELETE SET NULL because losing the
-- document shouldn't lose the conversation.
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS scoped_document_id UUID
    REFERENCES documents(id) ON DELETE SET NULL;

-- `summary` is a compact LLM-generated digest of the early messages. Read on
-- every turn that exceeds the live-history cap so context stays coherent
-- without re-sending hundreds of tokens.
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS summary TEXT;

-- Tracks the message count at which the current summary was generated, so the
-- background summarizer can detect when enough new turns have piled up to
-- justify another refresh. Stored separately from updated_at because that
-- column changes on every reply.
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS summary_turn_count INTEGER NOT NULL DEFAULT 0;

-- ── 1b. Messages: per-turn metadata (confidence band, etc.) ────────────────
-- Free-form JSON to keep this migration unobtrusive. Today only the confidence
-- band lives here ({"confidence": {"level": "...", "score": X, "n": Y}}); future
-- per-turn telemetry (e.g. tool counts, scope id) can join without another
-- ALTER. Stored as JSONB NOT NULL DEFAULT '{}' so reads don't have to guard
-- against null.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';

-- ── 2. Chunks: per-chunk embedding state (Day 11 #76) ───────────────────────
-- Three-state machine: pending (chunk row exists, not yet embedded), embedded
-- (success), failed (last attempt threw). Default to embedded for any rows
-- created BEFORE this migration so historical data doesn't all show "pending"
-- after deploy; new rows from `persist_chunks` set it explicitly.
ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS embedding_status TEXT NOT NULL DEFAULT 'embedded'
    CHECK (embedding_status IN ('pending', 'embedded', 'failed'));

-- Free-form message from the embedding provider. Kept for triage; the UI just
-- surfaces an aggregate count.
ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS embedding_error TEXT;

-- Counts attempts so we can bound how many times the reprocess loop will
-- chase a stubborn chunk.
ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;

-- Partial index — only failed chunks matter for the targeted-retry path,
-- and they're the minority of rows. Keeps the index tiny.
CREATE INDEX IF NOT EXISTS idx_chunks_document_failed
  ON chunks (document_id)
  WHERE embedding_status = 'failed';

-- ── 3. Documents: citation analytics (Day 12 #49) ───────────────────────────
-- Denormalised aggregate so the knowledge-intelligence dashboard doesn't have
-- to scan chunk_citations on every page load. Maintained by the chat path
-- when a message lands.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS citation_count INTEGER NOT NULL DEFAULT 0;

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS last_cited_at TIMESTAMPTZ;

-- Index for the "top documents" view; partial because unused docs are
-- excluded from the dashboard's hot panel.
CREATE INDEX IF NOT EXISTS idx_documents_citation_count
  ON documents (org_id, citation_count DESC)
  WHERE citation_count > 0;

-- ── 4. chunk_citations table (Day 12 #49) ───────────────────────────────────
-- One row per (chunk, message) pair the LLM actually cited. Powers:
--   * The retrieval boost (count rows grouped by document_id)
--   * The admin knowledge-intelligence dashboard
--   * Per-message audit of which chunks shaped which answer
CREATE TABLE IF NOT EXISTS chunk_citations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chunk_id        UUID NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- Raw vector cosine if available, otherwise the RRF score. Useful for
  -- weighting boost contributions later — a citation from a 0.95-cosine
  -- chunk is more meaningful than one from a 0.30-cosine fallback hit.
  similarity      REAL,
  cited_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A given chunk can only appear once per message (the orchestrator dedupes,
-- but defence-in-depth in case a retry re-runs the insert).
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunk_citations_unique
  ON chunk_citations (chunk_id, message_id);

CREATE INDEX IF NOT EXISTS idx_chunk_citations_org_doc
  ON chunk_citations (org_id, document_id);

CREATE INDEX IF NOT EXISTS idx_chunk_citations_message
  ON chunk_citations (message_id);

-- RLS — read for org members, writes only via service role.
ALTER TABLE chunk_citations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "chunk_citations_select" ON chunk_citations
  FOR SELECT USING (org_id = auth_org_id());

-- ── 5. RPC: knowledge-intelligence aggregates (Day 12 #49) ─────────────────
-- Service-role-callable RPC so the dashboard endpoint doesn't have to perform
-- three round trips. Returns top docs + total counts in one shot.
-- SECURITY DEFINER because the caller is the FastAPI service client and we
-- already validate org_id at the API layer.
CREATE OR REPLACE FUNCTION knowledge_intelligence(target_org_id UUID)
RETURNS TABLE (
  total_documents BIGINT,
  total_citations BIGINT,
  ready_documents BIGINT,
  cited_documents BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT
    (SELECT COUNT(*) FROM documents WHERE org_id = target_org_id)::BIGINT AS total_documents,
    (SELECT COUNT(*) FROM chunk_citations WHERE org_id = target_org_id)::BIGINT AS total_citations,
    (SELECT COUNT(*) FROM documents WHERE org_id = target_org_id AND status = 'ready')::BIGINT AS ready_documents,
    (SELECT COUNT(*) FROM documents WHERE org_id = target_org_id AND citation_count > 0)::BIGINT AS cited_documents;
$$;

-- ── 6. FTS: exclude non-embedded chunks ─────────────────────────────────────
-- The new ingest flow persists chunk rows BEFORE the embedding succeeds so we
-- can track per-chunk status. content_tsv is generated on insert, which means
-- pending/failed chunks would otherwise show up in FTS results — confusing,
-- and unfair to the user who'd see a "ghost" hit they can't reach via vector
-- search. Filter them out here so the hybrid pipeline stays consistent.
CREATE OR REPLACE FUNCTION fts_search(
  query_text        text,
  match_org_id      uuid,
  match_count       int  DEFAULT 10,
  match_document_id uuid DEFAULT NULL
)
RETURNS TABLE (
  chunk_id        uuid,
  content         text,
  document_id     uuid,
  document_name   text,
  chunk_index     int,
  page_number     int,
  section_heading text,
  similarity      float,
  snippet         text
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  tsq tsquery;
BEGIN
  tsq := websearch_to_tsquery('english', COALESCE(query_text, ''));

  IF tsq IS NULL OR tsq::text = '' THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    c.id                                                  AS chunk_id,
    c.content                                             AS content,
    c.document_id                                         AS document_id,
    d.name                                                AS document_name,
    c.chunk_index                                         AS chunk_index,
    c.page_number                                         AS page_number,
    c.section_heading                                     AS section_heading,
    ts_rank_cd(c.content_tsv, tsq, 32)::float             AS similarity,
    ts_headline(
      'english',
      c.content,
      tsq,
      'MaxFragments=2, MaxWords=18, MinWords=6, ShortWord=3, '
      || 'HighlightAll=false, StartSel=<mark>, StopSel=</mark>'
    )                                                     AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.org_id = match_org_id
    AND c.embedding_status = 'embedded'
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND c.content_tsv @@ tsq
  ORDER BY ts_rank_cd(c.content_tsv, tsq, 32) DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid) TO service_role;

-- ── 7. RPC: increment document citation counters (Day 12 #49) ──────────────
-- Atomic increment so two concurrent assistant messages can't race and lose
-- a count via read-modify-write at the application layer.
CREATE OR REPLACE FUNCTION bump_document_citations(doc_ids UUID[])
RETURNS VOID
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  UPDATE documents
  SET citation_count = citation_count + sub.cnt,
      last_cited_at = now()
  FROM (
    SELECT doc_id AS id, COUNT(*) AS cnt
    FROM unnest(doc_ids) AS doc_id
    GROUP BY doc_id
  ) AS sub
  WHERE documents.id = sub.id;
$$;

-- Lock down execute privileges. Anonymous never; authenticated never (the
-- chat path uses the service role to write); service_role explicitly so the
-- intent is documented at the schema level rather than relying on the
-- default GRANT TO PUBLIC behaviour.
REVOKE EXECUTE ON FUNCTION bump_document_citations(UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION bump_document_citations(UUID[]) TO service_role;

REVOKE EXECUTE ON FUNCTION knowledge_intelligence(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION knowledge_intelligence(UUID) TO service_role;
