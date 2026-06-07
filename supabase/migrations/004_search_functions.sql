-- ─── Vector search RPC ──────────────────────────────────────────────────────
-- Security model:
--   SECURITY INVOKER → function runs as the calling user, so RLS on chunks,
--   embeddings and documents fires for free. The match_org_id parameter is a
--   belt-and-suspenders filter; even if a caller passes the wrong value the
--   RLS policies block cross-org reads.
--
-- Recall vs latency:
--   SET LOCAL ivfflat.probes = 10 raises recall to ~95% (default of 1 gives
--   only ~70%, which is unacceptable for a RAG product whose output quality
--   depends on retrieved context).
--
-- Optional doc filter:
--   When match_document_id is non-null, results are restricted to that single
--   document — used by "summarize this doc" / "chat with this doc" flows.
--
-- Similarity metric:
--   We L2-normalize vectors at ingest time, so cosine distance and Euclidean
--   are equivalent. similarity = 1 - cosine_distance ∈ [0, 1] (higher = better).

CREATE OR REPLACE FUNCTION vector_search(
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
STABLE
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
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
  ORDER BY e.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Authenticated users can call it; RLS still controls what they see.
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION vector_search(vector, uuid, int, uuid) TO service_role;
