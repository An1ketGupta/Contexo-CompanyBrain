-- ─── Fix vector_search: SET inside non-VOLATILE function body is disallowed ─
--
-- Migration 004 declared the function STABLE but its body executed
--   SET LOCAL ivfflat.probes = 10;
-- which Postgres rejects at call-time with:
--   "SET is not allowed in a non-volatile function" (SQLSTATE 0A000).
--
-- Two ways to fix this:
--   (a) Move the GUC to a function-level SET attribute (cleanest).
--       Blocked on Supabase managed Postgres — `ivfflat.probes` is not in
--       the allowlist of GUCs that non-superusers may declare on a function.
--       Manifests as ERROR 42501: "permission denied to set parameter".
--   (b) Make the function VOLATILE so SET LOCAL inside the body is legal.
--       SET LOCAL at runtime is permitted for the authenticated role and is
--       transaction-scoped (auto-restored at function exit / txn end).
--
-- We take path (b). The VOLATILE downgrade has no real cost for an RPC-
-- invoked function — the planner can't inline it anyway (called over REST,
-- not from another SQL expression), so volatility classification doesn't
-- change query plans in practice. Recall benefit of probes=10 (~95% vs
-- ~70% at default probes=1) dominates.

DROP FUNCTION IF EXISTS vector_search(vector, uuid, int, uuid);

CREATE FUNCTION vector_search(
  query_embedding   vector(768),
  match_org_id      uuid,
  match_count       int    DEFAULT 10,
  match_document_id uuid   DEFAULT NULL
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
VOLATILE                       -- required so the SET LOCAL below is legal
SECURITY INVOKER
SET search_path = public       -- function-attribute SET (allowed by managed DB)
AS $$
BEGIN
  -- Raises IVFFlat recall to ~95%. Scoped to this transaction only.
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
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid) TO service_role;
