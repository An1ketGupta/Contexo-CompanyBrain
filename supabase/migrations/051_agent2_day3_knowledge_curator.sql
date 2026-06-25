-- ── Agent Roadmap 2 — Day 3: Knowledge Curator Agent (#24) ────────────────
--
-- The Curator is a weekly scan that produces a Knowledge Health Report
-- surfacing four classes of findings:
--
--   * Outdated documents     — last updated_at older than a configurable
--                              threshold (default 18 months / 540 days).
--   * Broken links           — http(s) URLs in document summaries / content
--                              that fail to resolve. SSRF-guarded.
--   * Merge candidate clusters — sets of documents with summary embeddings
--                                within ~0.75 cosine similarity. Reuses
--                                Day 2's `find_similar_documents` RPC at a
--                                lower threshold (the upload-time threshold
--                                is 0.85; "you might want to merge" is 0.75).
--   * Knowledge gaps         — clustered zero-result queries from `query_logs`
--                              and existing rows in `knowledge_gaps`. Stubs
--                              for the highest-volume gaps are drafted into
--                              the existing `document_drafts` table with
--                              source='knowledge_curator_scan' (per the
--                              "reuse, don't rebuild" decision).
--
-- The full report is persisted in `knowledge_health_reports` so admins can
-- compare week-over-week. We deliberately persist the whole payload as
-- JSONB (rather than normalising into per-finding tables) because:
--   1. Reports are append-only; we never query *inside* a report's findings
--      from SQL.
--   2. The shape will evolve as we add finding types — JSONB lets us add
--      fields without migrations.
--   3. Reads are always "give me this org's most recent report" which is a
--      cheap single-row fetch.
--
-- Per-org configuration lives on `organizations` (additive columns, all
-- nullable / default-having). No new settings table needed for v1; if the
-- surface grows we can pull it out later.

-- ── 1. knowledge_health_reports ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS knowledge_health_reports (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- 'pending' while the scan is in flight, then 'completed' or 'failed'.
  -- We persist the row at scan start so the admin UI can show "scan in
  -- progress…" without needing a separate state machine.
  status               TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'completed', 'failed')),

  -- The user who triggered an ad-hoc scan, or NULL for the weekly cron.
  triggered_by         UUID REFERENCES users(id) ON DELETE SET NULL,

  -- Full report payload. Shape:
  --   {
  --     "outdated_documents": [{id, name, updated_at, age_days}, ...],
  --     "broken_links":       [{document_id, document_name, url, reason}, ...],
  --     "merge_clusters":     [{members: [{id, name}, ...], avg_similarity}, ...],
  --     "knowledge_gaps":     [{topic, count, sample_queries: [...], draft_id?}, ...],
  --     "drafted_stubs":      [{draft_id, topic, gap_count}, ...],
  --     "counts": {outdated: N, broken_links: N, merge_clusters: N, gaps: N, stubs: N},
  --   }
  payload              JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Soft error reason when status='failed'. Truncated upstream to ~2KB.
  error_message        TEXT,

  -- Scan span. completed_at is NULL while pending.
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at         TIMESTAMPTZ
);

-- Hot read: "give me this org's most recent report." Indexed for descending
-- ORDER BY started_at; the partial filter on completed keeps the index lean
-- since pending rows are short-lived.
CREATE INDEX IF NOT EXISTS idx_knowledge_health_reports_org_started
  ON knowledge_health_reports(org_id, started_at DESC);

-- For history page: list all completed reports newest first.
CREATE INDEX IF NOT EXISTS idx_knowledge_health_reports_org_status
  ON knowledge_health_reports(org_id, status, started_at DESC);


ALTER TABLE knowledge_health_reports ENABLE ROW LEVEL SECURITY;

-- Read-only via the user JWT path; admins read these from the admin UI.
-- The router enforces role='admin' before issuing the read; the RLS policy
-- is org-isolation defence-in-depth.
DROP POLICY IF EXISTS knowledge_health_reports_select ON knowledge_health_reports;
CREATE POLICY knowledge_health_reports_select ON knowledge_health_reports
  FOR SELECT USING (org_id = auth_org_id());

-- Writes go through service-role (the Inngest worker).


COMMENT ON TABLE knowledge_health_reports IS
  'Agent2 Day 3 #24: weekly Knowledge Curator scan results. Payload shape documented in 051 migration.';


-- ── 2. Per-org Curator configuration ─────────────────────────────────────
--
-- Adding columns to `organizations` rather than a new table because there's
-- < ~5 settings here and they're all read together with the scan. A row
-- per setting would mean N round-trips per scan or a JSONB merge — pure
-- columns is the cheapest read.

ALTER TABLE organizations
  -- Enabled by default so existing orgs get the weekly digest without an
  -- opt-in step. Admins can flip this off from the Knowledge Health page.
  ADD COLUMN IF NOT EXISTS curator_enabled BOOLEAN NOT NULL DEFAULT true,

  -- Outdated threshold. 540 days = ~18 months — long enough that "I updated
  -- it last quarter" doesn't trigger, short enough to catch handbooks /
  -- pricing docs that have drifted. Tunable per-org from the UI.
  ADD COLUMN IF NOT EXISTS curator_outdated_days INTEGER NOT NULL DEFAULT 540
    CHECK (curator_outdated_days BETWEEN 30 AND 3650),

  -- Broken-link checks fan out N HTTP HEAD requests per scan. Some orgs
  -- have firewall'd intranet docs whose links won't resolve from our
  -- workers, producing noisy false positives. Default ON; let admins flip
  -- it OFF when noise outweighs signal.
  ADD COLUMN IF NOT EXISTS curator_check_broken_links BOOLEAN NOT NULL DEFAULT true,

  -- Merge-suggestion similarity threshold. 0.75 < the 0.85 upload-time
  -- duplicate threshold — "you may want to merge" is a softer signal than
  -- "this looks like a near-duplicate at ingest time."
  ADD COLUMN IF NOT EXISTS curator_merge_threshold FLOAT NOT NULL DEFAULT 0.75
    CHECK (curator_merge_threshold BETWEEN 0.5 AND 0.99);


COMMENT ON COLUMN organizations.curator_enabled IS
  'Agent2 Day 3 #24: gates the weekly Knowledge Curator scan for this org.';

COMMENT ON COLUMN organizations.curator_outdated_days IS
  'Agent2 Day 3 #24: documents not updated for this many days are flagged outdated.';

COMMENT ON COLUMN organizations.curator_check_broken_links IS
  'Agent2 Day 3 #24: HEAD-test http(s) URLs in documents during the scan.';

COMMENT ON COLUMN organizations.curator_merge_threshold IS
  'Agent2 Day 3 #24: cosine similarity for merge-cluster suggestions (lower than the 0.85 duplicate threshold).';
