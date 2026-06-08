-- ─── Full-text search RPC (Day 10) ──────────────────────────────────────────
-- Pairs with vector_search (migration 004) so the application layer can mix
-- them via Reciprocal Rank Fusion (Day 11 — HybridRetriever).
--
-- Design notes
-- ─────────────
--  • Parser: websearch_to_tsquery — handles quoted phrases ("Q4 revenue"),
--    OR operators, -exclusions, apostrophes and stray punctuation. Resilient
--    to anything a user might type, never raises on bad input.
--  • Ranking: ts_rank_cd(..., 32). Normalization flag 32 = rank/(rank+1),
--    which bounds scores to [0, 1) and avoids long-chunk bias that the
--    default ranking is prone to. The cover-density variant (cd) rewards
--    matches that cluster together rather than scattered single tokens.
--  • Snippet: ts_headline produces a highlighted excerpt for citation UI
--    (Day 20). Costs a re-scan of the matched chunk's text, but is cheap
--    at LIMIT 20 and avoids a second round-trip from the application.
--  • Empty query: if websearch_to_tsquery returns an empty tsquery (e.g.
--    only stopwords), we return zero rows so the hybrid layer can transparently
--    fall back to vector-only.
--  • Result shape: matches vector_search column-for-column (plus `snippet`)
--    so the Python retrieval adapter handles both branches uniformly.
--  • SECURITY INVOKER: function runs as the caller, so RLS on chunks/documents
--    fires. match_org_id is a belt-and-suspenders filter; cross-org reads
--    are blocked by RLS regardless of what the caller passes.

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

  -- Empty tsquery (no usable tokens) → return zero rows, no error.
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
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND c.content_tsv @@ tsq
  ORDER BY ts_rank_cd(c.content_tsv, tsq, 32) DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid) TO service_role;
