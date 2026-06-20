-- Add version metadata to retrieval RPCs so chat citations can show vN.
-- Keeps archived-chunk filtering intact while surfacing the version that
-- produced each chunk.

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
    AND (match_document_id IS NULL OR c.document_id = match_document_id)
    AND (match_document_ids IS NULL OR c.document_id = ANY(match_document_ids))
  ORDER BY similarity DESC
  LIMIT match_count;
END;
$$;

GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO authenticated;
GRANT EXECUTE ON FUNCTION fts_search(text, uuid, int, uuid, uuid[]) TO service_role;
