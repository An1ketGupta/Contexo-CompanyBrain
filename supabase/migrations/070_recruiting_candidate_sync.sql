-- ── 070: Candidate sync from ATS into the Notion hiring tracker ────────────
--
-- Why this exists
-- ---------------
-- The publish flow already creates a Notion tracker page with the JD + ATS
-- links, but the page is static. Recruiters then have to bounce between three
-- ATS UIs to see who applied. This migration adds the bones for an on-demand
-- "Sync Candidates" action that pulls applications from every connected ATS
-- and writes them as rows into a Notion database inside the tracker.
--
-- Structural changes
-- ------------------
-- 1. job_requisitions.notion_candidates_db_id   — Notion database id created
--    inside the tracker page at publish time. NULL when Notion was skipped
--    or pre-dates this migration.
-- 2. job_requisitions.candidates_last_synced_at — Last successful sync run.
--    Drives the "Last synced X minutes ago" label in the UI.
-- 3. job_requisitions.candidates_last_sync_error — Surfaces the most recent
--    sync failure so the UI can show an actionable card.
-- 4. recruiting_candidates table                — Local cache of every
--    candidate the sync pulled. The cache is the source of truth for
--    "which Notion page maps to which ATS application" so re-syncs do a
--    proper upsert rather than appending duplicates.
--
-- Naukri is explicitly excluded — its public API doesn't expose applicants
-- (Resdex is the candidate database, and that's a separate paid product).

ALTER TABLE job_requisitions
  ADD COLUMN IF NOT EXISTS notion_candidates_db_id TEXT,
  ADD COLUMN IF NOT EXISTS candidates_last_synced_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS candidates_last_sync_error TEXT;

COMMENT ON COLUMN job_requisitions.notion_candidates_db_id IS
  'Notion database id (child of the tracker page) where candidate rows are upserted by the candidate-sync flow.';
COMMENT ON COLUMN job_requisitions.candidates_last_synced_at IS
  'Timestamp of the last successful candidate sync. NULL when never synced.';
COMMENT ON COLUMN job_requisitions.candidates_last_sync_error IS
  'Most recent sync error (per-platform aggregated). NULL when last sync succeeded for every platform.';


-- ── recruiting_candidates ────────────────────────────────────────────────
-- One row per (ATS, external_id) candidate. Provides:
--   * idempotent upserts on resync (unique constraint on the natural key)
--   * a stable mapping to the Notion page so we PATCH the same row instead
--     of appending duplicates
--   * an audit trail of when each candidate was first seen / last refreshed

CREATE TABLE IF NOT EXISTS recruiting_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requisition_id UUID NOT NULL REFERENCES job_requisitions(id) ON DELETE CASCADE,
  ats_platform TEXT NOT NULL CHECK (ats_platform IN ('greenhouse','lever','ashby')),
  external_id TEXT NOT NULL,
  full_name TEXT,
  email TEXT,
  phone TEXT,
  current_company TEXT,
  current_title TEXT,
  stage TEXT,
  resume_url TEXT,
  candidate_url TEXT,
  applied_at TIMESTAMPTZ,
  raw_data JSONB,
  notion_page_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, requisition_id, ats_platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_recruiting_candidates_req
  ON recruiting_candidates (requisition_id, ats_platform);
CREATE INDEX IF NOT EXISTS idx_recruiting_candidates_org
  ON recruiting_candidates (org_id, updated_at DESC);

-- RLS: same model as job_requisitions — every member of the org can read,
-- writes happen only via the service-role client used by the sync job.
ALTER TABLE recruiting_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "recruiting_candidates_org_read" ON recruiting_candidates;
CREATE POLICY "recruiting_candidates_org_read" ON recruiting_candidates
  FOR SELECT TO authenticated
  USING (
    org_id IN (
      SELECT org_id FROM users WHERE id = auth.uid()
    )
  );

COMMENT ON TABLE recruiting_candidates IS
  'Local cache of candidates pulled from connected ATSes via the on-demand sync flow. notion_page_id maps each candidate to its row in the tracker''s Notion database.';


-- ── Widen audit-log action CHECK to include candidate_sync ──────────────────
-- Mirrors the pattern used in 068 for the ats_platform CHECK: drop whatever
-- the current constraint is named, recreate with the new set.
DO $$
DECLARE
  con record;
BEGIN
  FOR con IN
    SELECT conname
    FROM pg_constraint
    WHERE conrelid = 'recruiting_audit_log'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%action%'
  LOOP
    EXECUTE format('ALTER TABLE recruiting_audit_log DROP CONSTRAINT %I', con.conname);
  END LOOP;
END $$;

ALTER TABLE recruiting_audit_log
  ADD CONSTRAINT recruiting_audit_log_action_check CHECK (
    action IN (
      'publish_attempt',
      'ats_publish',
      'notion_create',
      'slack_notify',
      'hiring_manager_email',
      'edit',
      'delete',
      'candidate_sync'
    )
  );
