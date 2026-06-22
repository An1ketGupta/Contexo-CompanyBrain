-- ── V5 Days 3 + 4: LLM cost tracking, scheduled reports, embedding fine-tuning ──
--
-- Bundled because they all extend or sit beside `query_logs` and ship together.
--
--   1. query_logs cost columns + retrieved_chunk_ids
--      LLM cost dashboard (#75) tracks (input_tokens, output_tokens, cost_micros)
--      directly on each turn row — one source of truth that also powers the
--      `model_used` analytics we already have. Cost is stored in micros (USD * 1e6)
--      to avoid NUMERIC scale arithmetic in the hot path; sum/avg fit in BIGINT.
--      `retrieved_chunk_ids` captures every chunk the orchestrator surfaced
--      (cited + uncited) so Day-4 training-pair collection can build hard
--      negatives without re-running the search at feedback time.
--
--   2. scheduled_reports (#98)
--      User-defined recurring email reports (usage_summary, knowledge_health).
--      `next_send_at` is the only column the dispatch cron has to scan, so it
--      gets a covering index with `is_active` for the WHERE filter.
--
--   3. embedding_training_pairs (#106 Phase 1)
--      (query, positive_chunk, negative_chunks[]) triples harvested from
--      positive feedback (thumbs-up) and copy signals. signal_type lets us
--      weight copies vs. explicit thumbs differently when training.
--
--   4. embedding_fine_tune_jobs (#106 Phase 2)
--      One row per fine-tune attempt for an org. Tracks the lifecycle:
--      collecting_data → training → evaluating → deployed | failed.
--      `eval_score_before/after` (hit@5) gates auto-deploy.
--
-- All four sections are additive (no destructive ALTERs).


-- ── 1. query_logs: cost + retrieval-set columns ─────────────────────────────
ALTER TABLE query_logs
  ADD COLUMN IF NOT EXISTS input_tokens   INTEGER NOT NULL DEFAULT 0
    CHECK (input_tokens >= 0),
  ADD COLUMN IF NOT EXISTS output_tokens  INTEGER NOT NULL DEFAULT 0
    CHECK (output_tokens >= 0),
  -- USD * 1_000_000. Lets us store sub-cent costs (e.g. $0.000245) as
  -- integer 245 without NUMERIC rounding in SUM() / AVG() aggregations.
  ADD COLUMN IF NOT EXISTS cost_micros    BIGINT  NOT NULL DEFAULT 0
    CHECK (cost_micros >= 0),
  -- Full retrieval set for the turn (cited + uncited). Used by the
  -- training-pair collector to derive hard negatives. NOT a foreign-key
  -- array because chunk deletions are common and we don't want a cascade
  -- delete to nuke training history; the collector tolerates stale ids.
  ADD COLUMN IF NOT EXISTS retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}';

-- Internal LLM-cost dashboard queries SUM(cost_micros) per (org, day, model).
-- The existing (org_id, created_at DESC) index already covers the WHERE/GROUP
-- prefix; an additional partial index on cost_micros > 0 keeps the cost-
-- bearing-rows subset small and the dashboard fast even at 1M+ rows.
CREATE INDEX IF NOT EXISTS idx_query_logs_cost
  ON query_logs (created_at DESC)
  WHERE cost_micros > 0;

COMMENT ON COLUMN query_logs.cost_micros IS
  'V5 #75. USD cost * 1_000_000 (integer micros). Sum of all LLM rounds in the turn.';
COMMENT ON COLUMN query_logs.retrieved_chunk_ids IS
  'V5 #106 Phase 1. Every chunk surfaced by hybrid_search this turn (cited + uncited). '
  'Powers Day-4 training-pair hard-negative derivation.';


-- ── 2. scheduled_reports ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scheduled_reports (
  id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by    UUID        NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
  -- Free-form list — UI restricts to org members but we don't enforce that at
  -- the DB level because admins legitimately want to send reports to investors,
  -- board members, etc. that aren't in the Supabase users table.
  recipients    TEXT[]      NOT NULL CHECK (cardinality(recipients) BETWEEN 1 AND 20),
  frequency     VARCHAR(20) NOT NULL CHECK (frequency IN ('daily','weekly','monthly')),
  -- 0=Mon … 6=Sun. ISO 8601 weekday numbering matches Python's datetime.weekday().
  day_of_week   INTEGER     CHECK (day_of_week IS NULL OR (day_of_week BETWEEN 0 AND 6)),
  -- 1-28 only so February never silently shifts the calendar date.
  day_of_month  INTEGER     CHECK (day_of_month IS NULL OR (day_of_month BETWEEN 1 AND 28)),
  send_time_utc INTEGER     NOT NULL DEFAULT 8
                            CHECK (send_time_utc BETWEEN 0 AND 23),
  report_type   VARCHAR(50) NOT NULL
                            CHECK (report_type IN ('usage_summary','knowledge_health')),
  is_active     BOOLEAN     NOT NULL DEFAULT true,
  last_sent_at  TIMESTAMPTZ,
  next_send_at  TIMESTAMPTZ NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot path: the dispatch cron runs every 15 minutes and selects all rows where
-- is_active AND next_send_at <= now(). Partial index on is_active=true keeps
-- the index tiny relative to the table even if customers leave many archived.
CREATE INDEX IF NOT EXISTS idx_scheduled_reports_due
  ON scheduled_reports (next_send_at)
  WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_scheduled_reports_org
  ON scheduled_reports (org_id, created_at DESC);

ALTER TABLE scheduled_reports ENABLE ROW LEVEL SECURITY;

-- Members can SEE their org's reports (so non-admins can know what's being sent
-- about them) but only admins mutate. The mutation gate is enforced in the API
-- layer (router checks role='admin' before issuing the writer call), which we
-- mirror at the DB layer via a USING clause that filters writes to admin rows.
DROP POLICY IF EXISTS scheduled_reports_select ON scheduled_reports;
CREATE POLICY scheduled_reports_select
  ON scheduled_reports
  FOR SELECT
  USING (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
  );

DROP POLICY IF EXISTS scheduled_reports_insert ON scheduled_reports;
CREATE POLICY scheduled_reports_insert
  ON scheduled_reports
  FOR INSERT
  WITH CHECK (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
    AND EXISTS (
      SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

DROP POLICY IF EXISTS scheduled_reports_update ON scheduled_reports;
CREATE POLICY scheduled_reports_update
  ON scheduled_reports
  FOR UPDATE
  USING (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
    AND EXISTS (
      SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

DROP POLICY IF EXISTS scheduled_reports_delete ON scheduled_reports;
CREATE POLICY scheduled_reports_delete
  ON scheduled_reports
  FOR DELETE
  USING (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
    AND EXISTS (
      SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

COMMENT ON TABLE scheduled_reports IS
  'V5 #98. Per-org recurring email reports (usage_summary, knowledge_health). '
  'Dispatched by the inngest reports/dispatch-due cron every 15 minutes.';


-- ── 3. embedding_training_pairs ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS embedding_training_pairs (
  id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  query_text          TEXT        NOT NULL CHECK (char_length(query_text) <= 1000),
  -- Positive chunk — the one that actually got cited and received a positive
  -- signal. We do NOT cascade on chunks deletion: a doc deletion shouldn't
  -- nuke historical training data we already exported. Stale ids are filtered
  -- at JSONL-export time.
  positive_chunk_id   UUID        NOT NULL,
  -- Hard negatives — chunks retrieved by the same query but not cited.
  -- Same logic: no FK, dangling ids are tolerated.
  negative_chunk_ids  UUID[]      NOT NULL DEFAULT '{}',
  signal_type         VARCHAR(50) NOT NULL
                                  CHECK (signal_type IN ('copy','positive_feedback','high_confidence')),
  -- Tracks whether this pair has been included in an exported JSONL already.
  -- Lets us iterate retrains without re-uploading the same examples.
  exported_at         TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-org browse + retain. The cost dashboard / admin embeddings page hits
-- this index for the "training pairs collected" metric.
CREATE INDEX IF NOT EXISTS idx_training_pairs_org_created
  ON embedding_training_pairs (org_id, created_at DESC);

-- Idempotency: never persist the same (query, positive_chunk, signal_type)
-- twice from the same conversation. Prevents a user spamming thumbs-up from
-- skewing the training set. Multiple users hitting the same answer with
-- thumbs-up still records each as a vote — that's intentional.
CREATE UNIQUE INDEX IF NOT EXISTS uq_training_pair_dedup
  ON embedding_training_pairs (org_id, positive_chunk_id, md5(query_text), signal_type);

ALTER TABLE embedding_training_pairs ENABLE ROW LEVEL SECURITY;

-- Members can read their org's training data (transparency: "what data does
-- the fine-tune use?"). Writes go through the service-role collector only.
DROP POLICY IF EXISTS training_pairs_select ON embedding_training_pairs;
CREATE POLICY training_pairs_select
  ON embedding_training_pairs
  FOR SELECT
  USING (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
  );

COMMENT ON TABLE embedding_training_pairs IS
  'V5 #106 Phase 1. Contrastive (query, positive, negatives[]) triples from '
  'positive feedback + copy signals. Used to fine-tune embedding models per org.';


-- ── 4. embedding_fine_tune_jobs ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS embedding_fine_tune_jobs (
  id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  status                VARCHAR(50) NOT NULL DEFAULT 'pending'
                                    CHECK (status IN (
                                      'pending','collecting_data','training',
                                      'evaluating','deployed','failed','reembedding'
                                    )),
  -- Backend that ran the job. 'modal' = Modal.com serverless GPU running
  -- sentence-transformers. Stored so we can swap backends without losing
  -- the audit trail of which model lineage produced a given deployment.
  backend               VARCHAR(50) NOT NULL DEFAULT 'modal',
  base_model            VARCHAR(100) NOT NULL DEFAULT 'sentence-transformers/all-mpnet-base-v2',
  -- Identifier the backend returns for the trained artifact. For Modal this is
  -- the Modal deployment endpoint URL slug or an S3 path; not a foreign key,
  -- treated as opaque.
  fine_tuned_model_id   VARCHAR(500),
  training_pairs_count  INTEGER     NOT NULL DEFAULT 0,
  eval_score_before     REAL,       -- hit@5 with base model on held-out set
  eval_score_after      REAL,       -- hit@5 with fine-tuned model
  -- External job id from the backend so we can poll status without round-tripping.
  external_job_id       VARCHAR(255),
  triggered_by          UUID        REFERENCES users(id) ON DELETE SET NULL,
  error_message         TEXT,
  started_at            TIMESTAMPTZ,
  completed_at          TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ft_jobs_org_created
  ON embedding_fine_tune_jobs (org_id, created_at DESC);

-- "Is there an active job?" — the admin UI hits this on every page load.
-- Partial index keeps the hot subset tiny.
CREATE INDEX IF NOT EXISTS idx_ft_jobs_active
  ON embedding_fine_tune_jobs (org_id, status)
  WHERE status IN ('pending','collecting_data','training','evaluating','reembedding');

ALTER TABLE embedding_fine_tune_jobs ENABLE ROW LEVEL SECURITY;

-- Admin-only visibility. Members shouldn't see the eval score history etc.
DROP POLICY IF EXISTS ft_jobs_select ON embedding_fine_tune_jobs;
CREATE POLICY ft_jobs_select
  ON embedding_fine_tune_jobs
  FOR SELECT
  USING (
    org_id = (SELECT users.org_id FROM users WHERE users.id = auth.uid())
    AND EXISTS (
      SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

COMMENT ON TABLE embedding_fine_tune_jobs IS
  'V5 #106 Phase 2. One row per fine-tune attempt. Lifecycle: pending → '
  'training → evaluating → deployed | failed. Eval gate: only deploys if '
  'hit@5 improves over baseline.';


-- ── updated_at trigger for both new mutable tables ─────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_scheduled_reports_updated ON scheduled_reports;
CREATE TRIGGER trg_scheduled_reports_updated
  BEFORE UPDATE ON scheduled_reports
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_ft_jobs_updated ON embedding_fine_tune_jobs;
CREATE TRIGGER trg_ft_jobs_updated
  BEFORE UPDATE ON embedding_fine_tune_jobs
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
