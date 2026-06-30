-- ── 078: Interview Kit Agent + Knowledge Gap v2 ──────────────────────────
--
-- Two production agents land together because they share infra: both run
-- inside BaseAgent + Inngest, both surface to admins, and both want RLS-
-- gated, embedding-aware tables.
--
-- A. Interview Kit Agent (recruiting downstream of #20)
--    A recruiter selects a JD variant → presses "Generate Interview Kit"
--    on a published job_requisition → the InterviewKitAgent extracts
--    competencies, grounds them in company values pulled from the KB,
--    and emits 3 artifacts:
--      * panels       — interview stage × question set
--      * scorecard    — anchored 1-5 rubric per competency
--      * references   — reference-check question bank per relationship
--    Editable in-app, then "published" to lock it for the panel.
--
-- B. Knowledge Gap v2 (enhances #027)
--    The existing zero-hit pipeline (#027) ships drafts when a topic hits
--    3 hits / 7d. This widens it:
--      * detect low-confidence answers (conf<6 + sources<2), not just
--        zero-hit. The score-was-bad-but-we-answered case is the bulk of
--        platform-quality misses.
--      * persist a query embedding for clustering ("PTO" + "vacation
--        days" + "time off" → one cluster, one suggested doc).
--      * weekly cron writes a prioritized report so admins see the
--        platform-health picture, not just one-off draft pings.
--
-- Idempotent: every CREATE/ALTER guarded by IF NOT EXISTS / DROP IF EXISTS.

-- ════════════════════════════════════════════════════════════════════════
-- A. INTERVIEW KIT AGENT
-- ════════════════════════════════════════════════════════════════════════

-- One kit per (requisition, jd_variant). Keeping JSONB rather than splitting
-- panels/scorecard/refs into child tables: the kit is one editable artifact
-- the recruiter reviews as a whole. Per-row child tables would force a
-- transactional save flow without buying us any query that the JSONB shape
-- doesn't already serve. We get RLS at one row, the LLM emits one document,
-- and the admin edits one form.
CREATE TABLE IF NOT EXISTS interview_kits (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  requisition_id         UUID NOT NULL REFERENCES job_requisitions(id) ON DELETE CASCADE,
  created_by             UUID NOT NULL REFERENCES users(id),
  -- Cross-link to agent_runs so the admin audit page can navigate from a
  -- run to its kit. NULL until the agent fires (manual draft case).
  run_id                 UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  -- Which JD variant the kit was generated from. Useful for re-generation
  -- with a different variant + for "this kit reflects the published JD".
  jd_variant_index       INTEGER,
  role_title             TEXT,
  seniority_level        TEXT,
  -- Competencies derived from JD + values. Shape:
  --   [{name, kind: 'technical'|'behavioral'|'values'|'cultural',
  --     description, weight: 0..1}]
  competencies           JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Stages of the interview loop. Shape:
  --   [{stage, panelist_role, duration_minutes, focus,
  --     questions: [{question, competency, kind,
  --                  follow_ups: [...], what_good_looks_like}]}]
  panels                 JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Anchored rubric, one row per competency:
  --   [{competency, levels: [{score: 1..5, label, anchor,
  --                           behavioral_indicators: [...]}]}]
  scorecard              JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- Reference-check question banks per relationship:
  --   [{relationship: 'manager'|'peer'|'report'|'cross_functional',
  --     intro, questions: [{question, competency, why}]}]
  reference_questions    JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- KB documents the agent grounded the kit on. Same shape as the
  -- recruiting agent's `sources` so the UI can render a unified
  -- "Based on:" panel without per-agent rendering branches.
  sources                JSONB NOT NULL DEFAULT '[]'::jsonb,
  status                 TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'generating', 'ready', 'published', 'failed')),
  error_message          TEXT,
  generated_at           TIMESTAMPTZ,
  published_at           TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_interview_kits_org_created
  ON interview_kits(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interview_kits_requisition
  ON interview_kits(requisition_id, created_at DESC);

-- Touch updated_at on every UPDATE so the recruiter can see "last edited 3
-- minutes ago" without us hand-stamping it on every PATCH.
CREATE OR REPLACE FUNCTION interview_kits_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_interview_kits_touch_updated_at ON interview_kits;
CREATE TRIGGER trg_interview_kits_touch_updated_at
  BEFORE UPDATE ON interview_kits
  FOR EACH ROW EXECUTE FUNCTION interview_kits_touch_updated_at();

ALTER TABLE interview_kits ENABLE ROW LEVEL SECURITY;

-- Reads: any org member (interviewers need to see the kit). Recruiting is
-- org-shared work, same as job_requisitions.
CREATE POLICY "interview_kits_select" ON interview_kits
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "interview_kits_insert" ON interview_kits
  FOR INSERT WITH CHECK (org_id = auth_org_id() AND created_by = auth.uid());

CREATE POLICY "interview_kits_update" ON interview_kits
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND (created_by = auth.uid() OR EXISTS (
      SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin'
    ))
  );

CREATE POLICY "interview_kits_delete" ON interview_kits
  FOR DELETE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );


-- ════════════════════════════════════════════════════════════════════════
-- B. KNOWLEDGE GAP v2 — low-confidence + embeddings + weekly clustering
-- ════════════════════════════════════════════════════════════════════════

-- Extend the existing knowledge_gaps table (#027) with the v2 signals.
-- We don't fork into a new table because the existing pipeline + admin UI
-- already point at this row shape; v2 is a strict superset.
ALTER TABLE knowledge_gaps
  ADD COLUMN IF NOT EXISTS signal_type      TEXT NOT NULL DEFAULT 'zero_hit',
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC,
  ADD COLUMN IF NOT EXISTS sources_count    INT,
  ADD COLUMN IF NOT EXISTS query_embedding  vector(768),
  ADD COLUMN IF NOT EXISTS message_id       UUID REFERENCES messages(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS cluster_id       UUID;

ALTER TABLE knowledge_gaps DROP CONSTRAINT IF EXISTS knowledge_gaps_signal_check;
ALTER TABLE knowledge_gaps
  ADD CONSTRAINT knowledge_gaps_signal_check
  CHECK (signal_type IN ('zero_hit', 'low_confidence'));

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_signal
  ON knowledge_gaps(org_id, signal_type, created_at DESC);

-- ivfflat for the weekly clustering job. The cron reads one org's worth of
-- gaps and groups by cosine; we don't need probe tuning since the read is
-- batched (not interactive) — lists=50 gives a good speed/recall balance.
CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_embedding
  ON knowledge_gaps USING ivfflat (query_embedding vector_cosine_ops)
  WITH (lists = 50);


-- One row per (org, period). The cron picks a 7-day window each Monday and
-- emits a prioritized cluster list. The previous behavior (instant per-topic
-- AI stub drafts) stays — this report is the strategic complement.
CREATE TABLE IF NOT EXISTS knowledge_gap_reports (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id             UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  period_start       TIMESTAMPTZ NOT NULL,
  period_end         TIMESTAMPTZ NOT NULL,
  -- Top-line counts surfaced on the report card.
  total_gaps         INT NOT NULL DEFAULT 0,
  total_clusters     INT NOT NULL DEFAULT 0,
  zero_hit_count     INT NOT NULL DEFAULT 0,
  low_conf_count     INT NOT NULL DEFAULT 0,
  status             TEXT NOT NULL DEFAULT 'generating'
    CHECK (status IN ('generating', 'ready', 'failed')),
  error_message      TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gap_reports_org_created
  ON knowledge_gap_reports(org_id, created_at DESC);

-- Two crons firing in the same minute (one regular, one manual back-fill)
-- mustn't double-up a report for the same week.
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_gap_reports_period
  ON knowledge_gap_reports(org_id, period_start, period_end);

ALTER TABLE knowledge_gap_reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_gap_reports_select_admin ON knowledge_gap_reports;
CREATE POLICY knowledge_gap_reports_select_admin ON knowledge_gap_reports
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );


-- Clusters within a report. One row per topical bucket the clusterer
-- produced. Holds the rolled-up signals + the LLM-suggested doc scope.
CREATE TABLE IF NOT EXISTS knowledge_gap_clusters (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  report_id          UUID NOT NULL REFERENCES knowledge_gap_reports(id) ON DELETE CASCADE,
  -- LLM-named topic for the cluster (e.g. "PTO and vacation policy").
  topic              TEXT NOT NULL,
  -- LLM-suggested title an SME can use when authoring the doc.
  suggested_doc_title TEXT,
  -- LLM-drafted outline (bulleted scope). The SME fills in the answers.
  suggested_outline  TEXT,
  -- Aggregate signals used by the priority formula.
  query_count        INT NOT NULL DEFAULT 0,
  unique_users       INT NOT NULL DEFAULT 0,
  avg_confidence     NUMERIC,
  -- 0..1 — freq × recency × user_spread × conf_delta. Admins sort by this.
  priority_score     NUMERIC NOT NULL DEFAULT 0,
  -- Few sample queries shown verbatim in the admin UI so they can sanity-
  -- check the cluster cohesion before approving the suggested doc.
  sample_queries     JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- The actual knowledge_gaps rows the cluster grouped. Used by:
  --   * the admin "show all queries" drill-in
  --   * the analytics that compute coverage uplift after a doc lands
  gap_ids            UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
  resolution_status  TEXT NOT NULL DEFAULT 'open'
    CHECK (resolution_status IN ('open', 'in_progress', 'resolved', 'dismissed')),
  resolved_by        UUID REFERENCES users(id) ON DELETE SET NULL,
  resolved_at        TIMESTAMPTZ,
  resolution_doc_id  UUID REFERENCES documents(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gap_clusters_report
  ON knowledge_gap_clusters(report_id, priority_score DESC);

CREATE INDEX IF NOT EXISTS idx_knowledge_gap_clusters_org_status
  ON knowledge_gap_clusters(org_id, resolution_status, created_at DESC);

ALTER TABLE knowledge_gap_clusters ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_gap_clusters_select_admin ON knowledge_gap_clusters;
CREATE POLICY knowledge_gap_clusters_select_admin ON knowledge_gap_clusters
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Cluster updates (admins flipping resolution_status) go through service-
-- role; the admin router gates on role='admin' before issuing the update.


COMMENT ON TABLE interview_kits IS
  '078: InterviewKitAgent output — panels, anchored rubric, reference questions for a JD.';
COMMENT ON TABLE knowledge_gap_reports IS
  '078: Weekly prioritized knowledge-gap report (clustered missing-docs view).';
COMMENT ON TABLE knowledge_gap_clusters IS
  '078: One row per clustered topic inside a knowledge_gap_report.';
