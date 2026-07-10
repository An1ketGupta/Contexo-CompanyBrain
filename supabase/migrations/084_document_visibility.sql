-- ── Document visibility — private-by-default meeting transcripts ─────────────
-- Google Meet transcripts auto-sync from each user's own Drive folders. They
-- were landing in the shared, org-wide knowledge base immediately, so a private
-- 1:1 or confidential meeting became searchable by every teammate the moment it
-- was transcribed. This adds a per-document visibility gate:
--
--   'private' → visible ONLY to its creator (created_by). Excluded from every
--               other user's document list, chat retrieval, and — because the
--               search RPCs also filter on it — from all service_role callers
--               (background agents, MCP). The owner can still search it.
--   'org'     → the existing behavior: visible to the whole org.
--
-- Meeting transcripts are ingested 'private'; everything else defaults 'org'.
-- The owner publishes a transcript to the org KB explicitly (documents router).

ALTER TABLE documents
  ADD COLUMN visibility TEXT NOT NULL DEFAULT 'org'
    CHECK (visibility IN ('private', 'org'));

-- Existing auto-synced transcripts were already org-visible; retroactively make
-- them private so the fix is not defeated by rows ingested before this migration.
-- Owners can re-publish any they intend to share.
UPDATE documents SET visibility = 'private' WHERE source = 'google_meet_transcript';

-- Partial index: the only hot predicate is "hide private rows I don't own".
-- Private docs are a small slice, so a partial index stays tiny while covering
-- the RLS / RPC visibility filter.
CREATE INDEX idx_documents_private_owner
  ON documents (org_id, created_by)
  WHERE visibility = 'private';

-- ── RLS: extend the org-scoped SELECT with a visibility gate ─────────────────
-- Protects the user-scoped paths (document listing, direct chunk/preview reads,
-- and the SECURITY INVOKER search RPCs' JOIN onto documents).
DROP POLICY IF EXISTS "documents_select" ON documents;
CREATE POLICY "documents_select" ON documents
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (visibility = 'org' OR created_by = auth.uid())
  );

-- ── Search RPCs: enforce visibility inside the function ──────────────────────
-- The RPCs are SECURITY INVOKER, so for the user-scoped chat client RLS already
-- fires on the documents JOIN. But background agents and the MCP server call
-- these via the service_role client, where RLS is OFF. auth.uid() returns the
-- caller's id for a user JWT and NULL for service_role, so the same predicate
--   (d.visibility = 'org' OR d.created_by = auth.uid())
-- keeps private transcripts owner-only for users AND fully excludes them from
-- every service_role caller. Signatures/return shapes are unchanged from 041.

DROP FUNCTION IF EXISTS vector_search(vector, uuid, int, uuid, uuid[]);

CREATE FUNCTION vector_search(
  query_embedding    vector(768),
  match_org_id       uuid,
  match_count        int    DEFAULT 10,
  match_document_id  uuid   DEFAULT NULL,
  match_document_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  chunk_id            uuid,
  content             text,
  document_id         uuid,
  document_name       text,
  document_version_id uuid,
  version_number      int,
  chunk_index         int,
  page_number         int,
  section_heading     text,
  similarity          float
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
    c.document_version_id                             AS document_version_id,
    dv.version_number                                 AS version_number,
    c.chunk_index                                     AS chunk_index,
    c.page_number                                     AS page_number,
    c.section_heading                                 AS section_heading,
    (1 - (e.embedding <=> query_embedding))::float    AS similarity
  FROM embeddings e
  JOIN chunks   c  ON c.id = e.chunk_id
  JOIN documents d ON d.id = c.document_id
  LEFT JOIN document_versions dv ON dv.id = c.document_version_id
  WHERE e.org_id = match_org_id
    AND c.is_archived = false
    AND (d.visibility = 'org' OR d.created_by = auth.uid())
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
  chunk_id            uuid,
  content             text,
  document_id         uuid,
  document_name       text,
  document_version_id uuid,
  version_number      int,
  chunk_index         int,
  page_number         int,
  section_heading     text,
  similarity          float,
  snippet             text
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
    c.document_version_id                        AS document_version_id,
    dv.version_number                            AS version_number,
    c.chunk_index                                AS chunk_index,
    c.page_number                                AS page_number,
    c.section_heading                            AS section_heading,
    ts_rank_cd(c.content_tsv, q)::float          AS similarity,
    ts_headline('english', c.content, q,
      'MaxFragments=2, MaxWords=20, MinWords=8, '
      'StartSel=<mark>, StopSel=</mark>')        AS snippet
  FROM chunks c
  JOIN documents d ON d.id = c.document_id
  LEFT JOIN document_versions dv ON dv.id = c.document_version_id
  WHERE c.org_id = match_org_id
    AND c.is_archived = false
    AND c.content_tsv @@ q
    AND (d.visibility = 'org' OR d.created_by = auth.uid())
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO service_role;
