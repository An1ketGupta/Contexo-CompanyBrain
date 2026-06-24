-- ── Agent Roadmap 2 — Day 2: Duplicate Detection (#14) + Smart Routing (#33)
--
-- Two related additions:
--
-- 1. documents.summary_embedding (vector(768)) lets us compare two docs at
--    the document-level rather than the chunk-level. Chunk-level similarity
--    is great for retrieval but noisy for "are these two docs basically
--    the same?" — you can get a 0.9 chunk match between two HR policies
--    that share boilerplate but are otherwise different.
--
--    The summary embedding is generated once in
--    services/document_summary.py from the auto-summary text. Existing rows
--    get backfilled by a one-shot Inngest job
--    (app/inngest/duplicate_detection_functions.py::backfill_summary_embeddings).
--
--    IVFFlat with cosine — same opclass as the chunk embeddings index
--    (migration 001 line 79). Lists=50 is appropriate for a per-doc index
--    where we expect <100k rows per org. Bump to 100 once we're north of
--    50k docs in any single org.
--
-- 2. routing_suggestions stores Smart Routing's queue of "this doc would
--    fit in collection X if you added tag Y". Tag-based collections are
--    derived (documents.tags && collection.tag_filters), so we never write
--    a junction row — we just propose a tag addition that, once accepted,
--    makes the membership derive automatically.
--
-- The 'documents.tags' overlap path stays the single source of truth for
-- collection membership.

-- ── Summary embedding ────────────────────────────────────────────────────

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS summary_embedding vector(768),
  ADD COLUMN IF NOT EXISTS summary_embedded_at TIMESTAMPTZ;

-- IVFFlat cosine index. ``probes=10`` at query time is set by the search SQL,
-- not here — index build is fine with the default.
CREATE INDEX IF NOT EXISTS idx_documents_summary_embedding
  ON documents
  USING ivfflat (summary_embedding vector_cosine_ops)
  WITH (lists = 50);


-- ── find_similar_documents RPC ──────────────────────────────────────────
--
-- Returns the top N documents in the org whose summary embedding is within
-- 1 - threshold cosine distance of the source doc, excluding the source
-- itself. Distance < 0.15 ≈ similarity > 0.85 — Day 2's chosen threshold.
--
-- SECURITY DEFINER because the FastAPI caller passes service-role through
-- the duplicate-detection worker; the function still scopes by p_org_id
-- to avoid accidental cross-org reads.

CREATE OR REPLACE FUNCTION find_similar_documents(
  p_org_id          UUID,
  p_document_id     UUID,
  p_threshold       FLOAT DEFAULT 0.85,
  p_limit           INT   DEFAULT 5
)
RETURNS TABLE (
  doc_id            UUID,
  doc_name          TEXT,
  similarity        FLOAT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  WITH source AS (
    SELECT summary_embedding
      FROM documents
     WHERE id = p_document_id
       AND org_id = p_org_id
       AND summary_embedding IS NOT NULL
  )
  SELECT
    d.id   AS doc_id,
    d.name AS doc_name,
    1 - (d.summary_embedding <=> source.summary_embedding) AS similarity
  FROM documents d, source
  WHERE d.org_id = p_org_id
    AND d.id <> p_document_id
    AND d.status = 'ready'
    AND d.summary_embedding IS NOT NULL
    AND (1 - (d.summary_embedding <=> source.summary_embedding)) >= p_threshold
  ORDER BY d.summary_embedding <=> source.summary_embedding ASC
  LIMIT p_limit;
$$;


-- ── Routing suggestions queue ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS routing_suggestions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  collection_id     UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,

  -- The single tag we'd add to documents.tags to bring the doc into the
  -- collection. Pick from the collection's tag_filters at suggestion time;
  -- snapshotted here so a later filter edit doesn't silently change the
  -- pending suggestion's effect on accept.
  suggested_tag     TEXT NOT NULL,

  -- 0..1 cosine similarity between the doc's summary embedding and the
  -- collection's centroid. Surfaced in the admin UI for sanity-checking.
  similarity        FLOAT NOT NULL CHECK (similarity >= 0 AND similarity <= 1),

  status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'accepted', 'rejected')),

  decided_by        UUID REFERENCES users(id) ON DELETE SET NULL,
  decided_at        TIMESTAMPTZ,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One open suggestion per (document, collection). Re-running smart-routing
-- on a doc that already has a pending suggestion for the same collection
-- is a no-op rather than a queue duplicate.
CREATE UNIQUE INDEX IF NOT EXISTS uq_routing_suggestions_pending_doc_collection
  ON routing_suggestions(document_id, collection_id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_routing_suggestions_org_status
  ON routing_suggestions(org_id, status, created_at DESC);


ALTER TABLE routing_suggestions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS routing_suggestions_select ON routing_suggestions;
CREATE POLICY routing_suggestions_select ON routing_suggestions
  FOR SELECT USING (org_id = auth_org_id());

-- Writes go through service-role (admin endpoints check role themselves).


COMMENT ON COLUMN documents.summary_embedding IS
  'Agent2 Day 2 #14: doc-level embedding of metadata.summary. Used for duplicate detection + smart routing centroids.';

COMMENT ON TABLE routing_suggestions IS
  'Agent2 Day 2 #33: queue of "add tag X to doc Y to fold it into collection Z" proposals from smart routing.';
