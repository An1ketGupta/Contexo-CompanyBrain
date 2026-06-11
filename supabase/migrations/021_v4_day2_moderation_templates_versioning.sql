-- ── V4 Day 2: moderation, template variables, document versioning ───────────
--
-- Three independent additions that ship together:
--   1. moderation_logs        — record blocked/flagged user inputs
--   2. prompt_templates.variables — JSON array of placeholder definitions
--   3. document_versions + chunks.{is_archived, document_version_id}
--   4. Updated vector_search / fts_search to exclude archived chunks
--
-- All RLS is enforced; service-role does the writes that don't fit RLS
-- (moderation_logs INSERT, version archival on chunks).

-- ── 1. moderation_logs ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS moderation_logs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- NULLable so we can log moderation on unauthenticated public-API calls if
  -- we ever expose one. Today it's always set; we don't enforce NOT NULL.
  user_id       UUID REFERENCES users(id) ON DELETE SET NULL,
  -- The exact text that tripped the filter, truncated to 500 chars at the
  -- application layer. We KEEP this — admins need it to tune the patterns
  -- and to see what their users are trying to do.
  query_text    TEXT NOT NULL,
  result        TEXT NOT NULL CHECK (result IN ('blocked', 'flagged')),
  -- Which pattern fired. Free-form so the catalog can grow without migrations.
  reason        TEXT,
  -- Distinguishes "we stopped the request" from "we logged and proceeded".
  action_taken  TEXT NOT NULL CHECK (action_taken IN ('rejected', 'logged_and_proceeded')),
  -- Admin marks an entry as reviewed once they've triaged it. Defers a "needs
  -- attention" badge once we surface it in the UI; for now it's documentation.
  reviewed_by   UUID REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_moderation_logs_org_time
  ON moderation_logs (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_moderation_logs_org_result_time
  ON moderation_logs (org_id, result, created_at DESC);

ALTER TABLE moderation_logs ENABLE ROW LEVEL SECURITY;

-- Admin-only read; service role writes. The admin check uses the same
-- non-recursive pattern as prompt_templates' update policy.
DROP POLICY IF EXISTS moderation_logs_select ON moderation_logs;
CREATE POLICY moderation_logs_select ON moderation_logs
  FOR SELECT
  USING (
    org_id = auth_org_id()
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
  );

-- ── 2. prompt_templates.variables ──────────────────────────────────────────
-- JSONB array of:
--   { name: string, label: string, placeholder: string, required: boolean }
-- The backend auto-extracts {{variable}} placeholders from template_text on
-- write when this field is empty, so old (V3) rows continue to work.
ALTER TABLE prompt_templates
  ADD COLUMN IF NOT EXISTS variables JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ── 3. document_versions ───────────────────────────────────────────────────
-- Each upload of "the same document" mints a new version row. The latest
-- row is the only one with is_current=true. We DON'T delete old rows when
-- a new version arrives — the audit trail ("who replaced what, when") is
-- a feature, and the cost is one row per version with a TEXT file_path.
CREATE TABLE IF NOT EXISTS document_versions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_number   INTEGER NOT NULL CHECK (version_number >= 1),
  file_path        TEXT NOT NULL,
  file_size_bytes  BIGINT,
  uploaded_by      UUID REFERENCES users(id) ON DELETE SET NULL,
  is_current       BOOLEAN NOT NULL DEFAULT false,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(document_id, version_number)
);

-- One current version per document. Partial unique index lets multiple rows
-- coexist with is_current=false, only blocks two "current" rows for the
-- same document.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_document_versions_one_current
  ON document_versions (document_id)
  WHERE is_current = true;

CREATE INDEX IF NOT EXISTS idx_document_versions_doc_number
  ON document_versions (document_id, version_number DESC);

ALTER TABLE document_versions ENABLE ROW LEVEL SECURITY;

-- Read: any member can see the version history of any document in their org
-- (mirrors the documents.select policy).
DROP POLICY IF EXISTS document_versions_select ON document_versions;
CREATE POLICY document_versions_select ON document_versions
  FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.documents d
      WHERE d.id = document_versions.document_id
        AND d.org_id = auth_org_id()
    )
  );

-- ── 4. chunks: archival + version pointer ──────────────────────────────────
-- A version bump archives every existing chunk and re-ingests the new file.
-- Vector + FTS searches must skip is_archived=true rows so old content
-- doesn't leak into new answers. We KEEP the rows because chunk_citations
-- references them (and a hard delete would cascade-nuke historical
-- analytics).
ALTER TABLE chunks
  ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false,
  -- NULL = "this chunk pre-dates versioning" (legacy V3 chunks).
  ADD COLUMN IF NOT EXISTS document_version_id UUID
    REFERENCES document_versions(id) ON DELETE SET NULL;

-- Hot path filter: hybrid_search reads chunks WHERE is_archived = false.
-- Partial index keeps it tight; older archived rows don't pollute it.
CREATE INDEX IF NOT EXISTS idx_chunks_doc_active
  ON chunks (document_id) WHERE is_archived = false;

-- ── 5. Re-create vector_search / fts_search to filter archived chunks ──────
-- Same signature as migration 018; we just add the is_archived filter. Old
-- callers continue to work; new callers automatically benefit.

DROP FUNCTION IF EXISTS vector_search(vector, uuid, int, uuid, uuid[]);

CREATE FUNCTION vector_search(
  query_embedding    vector(768),
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id        uuid,
  content         text,
  document_id     uuid,
  document_name   text,
  chunk_index     int,
  page_number     int,
  section_heading text,
  similarity      float
)
LANGUAGE plpgsql
VOLATILE
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
  SET LOCAL ivfflat.probes = 10;

  RETURN QUERY
  SELECT
    c.id                                              AS chunk_id,
    c.content                                         AS content,
    c.document_id                                     AS document_id,
    d.name                                            AS document_name,
    c.chunk_index                                     AS chunk_index,
    c.page_number                                     AS page_number,
    c.section_heading                                 AS section_heading,
    (1 - (e.embedding <=> query_embedding))::float    AS similarity
  FROM embeddings e
  JOIN chunks   c ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.document_id
  WHERE e.org_id = match_org_id
    AND c.is_archived = false
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid, uuid[]) TO service_role;

DROP FUNCTION IF EXISTS fts_search(text, uuid, int, uuid, uuid[]);

CREATE FUNCTION fts_search(
  query_text         text,
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
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
  q tsquery := websearch_to_tsquery('english', query_text);
BEGIN
  RETURN QUERY
  SELECT
    c.id                                         AS chunk_id,
    c.content                                    AS content,
    c.document_id                                AS document_id,
    d.name                                       AS document_name,
    c.chunk_index                                AS chunk_index,
    c.page_number                                AS page_number,
    c.section_heading                            AS section_heading,
    ts_rank_cd(c.content_tsv, q)::float          AS similarity,
    ts_headline('english', c.content, q,
      'MaxFragments=2, MaxWords=20, MinWords=8, '
      'StartSel=<mark>, StopSel=</mark>')        AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  WHERE c.org_id = match_org_id
    AND c.is_archived = false
    AND c.content_tsv @@ q
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO service_role;
