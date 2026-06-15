-- ── Agent Roadmap Day 9 — Policy Change Propagation Autoflow
--
-- Three additive surfaces — none of them touch existing data paths:
--
--   1. document_versions gets two AI-only columns: chunk_summary (the
--      LLM-generated précis of the version's content) and content_hash
--      (sha256 of the source text, used to short-circuit "nothing changed"
--      reprocessing). Both nullable so legacy rows continue to work.
--
--   2. document_diffs — one row per from→to comparison the policy agent
--      runs. Owned by the agent; admins read it through the doc detail
--      page. We KEEP every diff (no GC) because the audit trail is the
--      compliance value prop here.
--
--   3. acknowledgements — one row per (document_version, user). The agent
--      bulk-inserts on policy propagation; users update with /compliance
--      endpoints. Reminder cadence lives in the row (reminder_count +
--      last_reminder_at) so the daily cron is a pure SQL filter.
--
-- Two columns on documents:
--   * requires_acknowledgement — admin-set boolean. The propagation agent
--     fires for any doc tagged 'policy' OR with this flag true.
--   * current_version_id — convenience pointer to the latest acknowledged
--     version. Materialised by the agent on completion so the dashboard
--     can render "you've acknowledged v3, current is v4" without a join.

-- ── 1. document_versions: chunk_summary + content_hash ─────────────────────
ALTER TABLE document_versions
  ADD COLUMN IF NOT EXISTS chunk_summary TEXT,
  ADD COLUMN IF NOT EXISTS content_hash  TEXT;

CREATE INDEX IF NOT EXISTS idx_document_versions_content_hash
  ON document_versions (document_id, content_hash);

COMMENT ON COLUMN document_versions.chunk_summary IS
  'Day 9: LLM-generated ~500-char summary of the version''s content. Fed into the diff prompt on the NEXT version so we don''t pay to re-summarise the whole doc on every change.';
COMMENT ON COLUMN document_versions.content_hash IS
  'Day 9: sha256 of the version''s extracted text. The propagation agent compares against the previous version''s hash and skips diff generation when unchanged (re-uploads of the same file).';


-- ── 2. document_diffs ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_diffs (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id       UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,

  from_version_id   UUID REFERENCES document_versions(id) ON DELETE SET NULL,
  to_version_id     UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  from_version      INTEGER,
  to_version        INTEGER NOT NULL,

  diff_summary      TEXT NOT NULL,
  agent_run_id      UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- One diff per (doc, to_version) — repeated agent runs on the same
  -- version land on the same row (UPSERT-style) rather than duplicating.
  UNIQUE (document_id, to_version_id)
);

CREATE INDEX IF NOT EXISTS idx_document_diffs_doc_created
  ON document_diffs (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_diffs_org_created
  ON document_diffs (org_id, created_at DESC);

ALTER TABLE document_diffs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS document_diffs_select ON document_diffs;
CREATE POLICY document_diffs_select ON document_diffs
  FOR SELECT USING (org_id = auth_org_id());


-- ── 3. acknowledgements ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS acknowledgements (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id           UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  document_version_id   UUID REFERENCES document_versions(id) ON DELETE CASCADE,
  user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  status                TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'acknowledged', 'dismissed')),
  acknowledged_at       TIMESTAMPTZ,
  reminder_count        INTEGER NOT NULL DEFAULT 0,
  last_reminder_at      TIMESTAMPTZ,

  agent_run_id          UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  diff_id               UUID REFERENCES document_diffs(id) ON DELETE SET NULL,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- One ack row per (doc-version, user). A user re-acknowledges a NEW
  -- version because that's a new document_version_id.
  UNIQUE (document_id, user_id, document_version_id)
);

-- Hot indexes:
--   * banner query: WHERE user_id=:me AND status='pending'
--   * admin report: WHERE org_id=:org AND document_id=:doc
--   * reminder cron: WHERE status='pending' AND created_at <= threshold
CREATE INDEX IF NOT EXISTS idx_ack_user_status
  ON acknowledgements (user_id, status);
CREATE INDEX IF NOT EXISTS idx_ack_org_doc
  ON acknowledgements (org_id, document_id);
CREATE INDEX IF NOT EXISTS idx_ack_pending_age
  ON acknowledgements (org_id, status, created_at)
  WHERE status = 'pending';

ALTER TABLE acknowledgements ENABLE ROW LEVEL SECURITY;

-- Users see their own rows; admins see everything in the org.
DROP POLICY IF EXISTS acknowledgements_select_self_or_admin ON acknowledgements;
CREATE POLICY acknowledgements_select_self_or_admin ON acknowledgements
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      user_id = auth.uid()
      OR EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
    )
  );

-- Users update their own rows (acknowledge / dismiss). The service role
-- handles bulk inserts (agent fan-out) + reminder bookkeeping.
DROP POLICY IF EXISTS acknowledgements_update_self ON acknowledgements;
CREATE POLICY acknowledgements_update_self ON acknowledgements
  FOR UPDATE USING (
    org_id = auth_org_id() AND user_id = auth.uid()
  ) WITH CHECK (
    org_id = auth_org_id() AND user_id = auth.uid()
  );


-- ── 4. documents: requires_acknowledgement + current_version_id ────────────
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS requires_acknowledgement BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS current_version_id      UUID
    REFERENCES document_versions(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS last_propagated_at      TIMESTAMPTZ;

COMMENT ON COLUMN documents.requires_acknowledgement IS
  'Day 9: when TRUE, every member must acknowledge each new version. Also fires the policy propagation agent on processing complete. Admins set this; the agent reads it.';
COMMENT ON COLUMN documents.current_version_id IS
  'Day 9: convenience pointer to the version row the propagation agent treated as latest. Avoids a sub-query on the dashboard.';
COMMENT ON COLUMN documents.last_propagated_at IS
  'Day 9: timestamp of the most recent propagation run. The trigger condition compares to processed_at to skip stale re-fires.';

CREATE INDEX IF NOT EXISTS idx_documents_requires_ack
  ON documents (org_id)
  WHERE requires_acknowledgement = true;


-- ── 5. organizations.metadata: compliance_config defaults ─────────────────
-- We DON'T add a column — config lives in the existing JSONB so the org
-- settings UI uses one update path. Shape:
--   {
--     "compliance": {
--       "reminder_threshold_days": 3,
--       "max_reminders": 3,
--       "reminder_cadence_days": 1,
--       "auto_propagate_policy_tag": true,
--       "policy_channel_id": "C0123ABCD"   -- optional Slack channel id
--     }
--   }
-- All keys optional; the application layer falls back to safe defaults.
COMMENT ON COLUMN organizations.metadata IS
  'Org config JSONB. Day 3 added confidence_thresholds; Day 9 reserves a `compliance` key for ack reminder cadence and the default Slack channel for policy posts.';
