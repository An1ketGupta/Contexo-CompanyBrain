-- ── 056 — Knowledge Graph Snapshots (Feature 2.1)
--
-- Persisted, time-stamped clustering of the org's documents by their
-- summary_embedding. A nightly Inngest cron runs k-means + LLM cluster
-- naming and writes one snapshot row per org. The admin /knowledge-graph
-- page reads the latest snapshot — no on-demand compute on the request
-- path. Manual refresh = trigger the same Inngest function inline.
--
-- Shape:
--   • clusters JSONB   — array of {id, label, color, summary, doc_count,
--                                  centroid_doc_ids[]}
--   • nodes    JSONB   — array of {id, name, cluster_id, file_type,
--                                  health_label, citation_count}
--   • edges    JSONB   — array of {source, target, weight}
--
-- Edges currently encode cross-cluster co-citation (two docs cited in the
-- same chat turn ≥ N times). They're optional: the v1 UI renders a
-- force-directed layout from clusters + nodes without needing edges,
-- which the worker fills in as a second pass.

CREATE TABLE IF NOT EXISTS knowledge_graph_snapshots (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- Generation metadata — surfaced in the UI for "last computed N hours ago"
  -- and so admins can correlate snapshot quality with the doc base size.
  doc_count     INTEGER NOT NULL DEFAULT 0,
  cluster_count INTEGER NOT NULL DEFAULT 0,
  -- "ok" once the worker successfully wrote the snapshot; "computing" for
  -- in-flight, "failed" if naming or persistence blew up. The latest row
  -- per org with status='ok' is the one the UI reads.
  status        TEXT NOT NULL DEFAULT 'computing'
    CHECK (status IN ('computing', 'ok', 'failed')),
  error_message TEXT,
  clusters      JSONB NOT NULL DEFAULT '[]'::JSONB,
  nodes         JSONB NOT NULL DEFAULT '[]'::JSONB,
  edges         JSONB NOT NULL DEFAULT '[]'::JSONB,
  -- Free-form params dict for the run — embedding model, k chosen,
  -- iterations, etc. Useful when diffing snapshots over time.
  params        JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot path: "give me the latest ok snapshot for this org" — single index
-- scan, no sort cost.
CREATE INDEX IF NOT EXISTS idx_kg_snapshots_org_latest
  ON knowledge_graph_snapshots (org_id, created_at DESC)
  WHERE status = 'ok';

CREATE INDEX IF NOT EXISTS idx_kg_snapshots_org_status
  ON knowledge_graph_snapshots (org_id, status, created_at DESC);

ALTER TABLE knowledge_graph_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "kg_snapshots_select" ON knowledge_graph_snapshots;
CREATE POLICY "kg_snapshots_select" ON knowledge_graph_snapshots
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );

-- No INSERT/UPDATE/DELETE policies — writes happen via the service-role
-- worker only. Manual delete from the dashboard is fine to add later.

COMMENT ON TABLE knowledge_graph_snapshots IS
  'Feature 2.1: nightly k-means + LLM-named cluster snapshot of an org''s documents. Read by the admin Knowledge Graph page; written by the Inngest worker `knowledge_graph_refresh`.';


-- ── Retention: prune snapshots older than 30 days, keeping the latest 5 ──
-- Cap storage growth. We keep more than just the latest so admins can scrub
-- backward through change in cluster shape; 5 weekly snapshots covers a
-- typical "what changed since last month" window.
CREATE OR REPLACE FUNCTION prune_old_kg_snapshots()
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  removed INTEGER := 0;
BEGIN
  WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
             PARTITION BY org_id
             ORDER BY created_at DESC
           ) AS rn
      FROM knowledge_graph_snapshots
     WHERE status = 'ok'
  )
  DELETE FROM knowledge_graph_snapshots g
   USING ranked r
   WHERE g.id = r.id
     AND r.rn > 5
     AND g.created_at < NOW() - INTERVAL '30 days';
  GET DIAGNOSTICS removed = ROW_COUNT;
  RETURN removed;
END
$$;

COMMENT ON FUNCTION prune_old_kg_snapshots() IS
  'Feature 2.1: deletes snapshots that are both older than 30 days AND not in the most recent 5 per org. Called from the nightly Inngest function.';
