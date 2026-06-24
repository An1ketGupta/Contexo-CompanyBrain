
-- ── Agent Roadmap 2 — Day 1: Autoflow Engine + generalized approvals
--
-- The autoflow engine is the shared trigger/action backbone that Sequences
-- (#8), Knowledge Curator (#24), and Internal Comms (#23) will sit on. Two
-- new tables (autoflows definitions + autoflow_runs execution log) plus
-- modifications to the existing `approvals` table so that an autoflow run
-- can be gated on human approval the same way a message can.
--
-- Design choices worth keeping in mind for downstream changes:
--
-- 1. trigger_type is a TEXT CHECK enum, NOT a Postgres ENUM. We need to be
--    able to add new trigger types without an ALTER TYPE migration that locks
--    every dependent table. Same reasoning for action types — those live in
--    the JSONB payload, validated at the Pydantic layer (see app/models/autoflow.py).
--
-- 2. trigger_config + actions are JSONB. Validating the shape in SQL would
--    require either a heavy CHECK with JSON Schema (unsupported pre-PG15) or
--    fragile per-field constraints. We validate on the FastAPI write path
--    (Pydantic) and trust storage. The dispatcher tolerates malformed actions
--    by failing that run, not the whole table.
--
-- 3. last_fired_at lets the scheduled-cron dispatcher dedupe: a cron like
--    "0 9 * * *" matches every second of the minute, so the dispatcher checks
--    "did this fire within the last 60s already?" before re-firing. Without
--    this, a brief dispatcher restart could double-fire a flow.
--
-- 4. confidence_threshold only applies to actions that carry an LLM-derived
--    confidence (e.g. generate_output) — for deterministic actions (send_email,
--    post_slack, webhook) the gate is a no-op. This is documented in
--    app/services/autoflow_actions.py; not enforced in the DB.
--
-- 5. autoflow_runs.steps captures per-action status so a partially-failed run
--    is debuggable in the admin UI without rebuilding the timeline from logs.
--    Cap kept small (JSONB) — there's a hard limit of 32 actions per autoflow,
--    enforced in Pydantic.
--
-- Approvals generalization:
--   * The existing approvals table was tied 1:1 to a message_id. To let an
--     autoflow_run be approved the same way (web/email/slack), we add a
--     subject_type + subject_id pair, make message_id nullable, backfill,
--     and switch the pending-uniqueness index to (subject_type, subject_id,
--     approver_id). Existing message-based approvals continue to work
--     unchanged.

-- ── Autoflows ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS autoflows (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  name                 TEXT NOT NULL,
  description          TEXT,

  -- See app/models/autoflow.py for the canonical enum. Keep this in sync.
  trigger_type         TEXT NOT NULL,
  trigger_config       JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Ordered list of {type, config, order} dicts. Validated by Pydantic.
  actions              JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- When set, generate_output actions emitting below this confidence pause
  -- the run and create an approval row. NULL = never gate.
  confidence_threshold FLOAT,

  is_active            BOOLEAN NOT NULL DEFAULT true,

  -- Dedupe key for the scheduled cron dispatcher; nullable for non-scheduled
  -- triggers. Persists so a dispatcher restart can't double-fire.
  last_fired_at        TIMESTAMPTZ,

  created_by           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT autoflows_trigger_type_check
    CHECK (trigger_type IN (
      'document_uploaded',
      'document_ready',
      'document_failed',
      'query_no_results',
      'message_feedback_negative',
      'scheduled',
      'employee_joined',
      'knowledge_gap_detected',
      'approval_requested',
      'agent_completed',
      'compliance_acknowledged'
    )),
  CONSTRAINT autoflows_confidence_range
    CHECK (confidence_threshold IS NULL OR (confidence_threshold >= 0 AND confidence_threshold <= 1)),
  CONSTRAINT autoflows_name_len
    CHECK (char_length(name) BETWEEN 1 AND 120)
);

-- Hot path: dispatcher fetch by (org_id, trigger_type) where active.
CREATE INDEX IF NOT EXISTS idx_autoflows_org_trigger_active
  ON autoflows(org_id, trigger_type)
  WHERE is_active = true;

-- Dedicated cover for the scheduled cron — it scans every active scheduled
-- autoflow once a minute and needs to be cheap.
CREATE INDEX IF NOT EXISTS idx_autoflows_scheduled_active
  ON autoflows(trigger_type, is_active)
  WHERE trigger_type = 'scheduled' AND is_active = true;


-- ── Autoflow runs (execution log) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS autoflow_runs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  autoflow_id          UUID NOT NULL REFERENCES autoflows(id) ON DELETE CASCADE,
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  trigger_payload      JSONB NOT NULL DEFAULT '{}'::jsonb,

  status               TEXT NOT NULL DEFAULT 'pending',

  -- Per-step status — array of {index, type, status, started_at, completed_at,
  -- error?, output?}. Populated by the executor as it walks the actions list.
  steps                JSONB NOT NULL DEFAULT '[]'::jsonb,

  steps_completed      INT NOT NULL DEFAULT 0,
  total_steps          INT NOT NULL DEFAULT 0,
  error_message        TEXT,

  -- Set when status='held_for_approval' so the resume path can find which
  -- approvals row gates this run.
  blocking_approval_id UUID REFERENCES approvals(id) ON DELETE SET NULL,

  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at         TIMESTAMPTZ,

  CONSTRAINT autoflow_runs_status_check
    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'held_for_approval', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_autoflow_runs_autoflow_started
  ON autoflow_runs(autoflow_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_autoflow_runs_org_status
  ON autoflow_runs(org_id, status, started_at DESC);

-- Used by the resume-after-approval path.
CREATE INDEX IF NOT EXISTS idx_autoflow_runs_blocking_approval
  ON autoflow_runs(blocking_approval_id)
  WHERE blocking_approval_id IS NOT NULL;


-- ── Generalize approvals: support autoflow_run subjects ────────────────────

-- Step 1: add subject columns nullable so existing rows pass.
ALTER TABLE approvals
  ADD COLUMN IF NOT EXISTS subject_type TEXT,
  ADD COLUMN IF NOT EXISTS subject_id UUID;

-- Step 2: backfill from message_id.
UPDATE approvals
   SET subject_type = 'message',
       subject_id   = message_id
 WHERE subject_type IS NULL
   AND message_id IS NOT NULL;

-- Step 3: relax the NOT NULL on message_id so autoflow approvals don't need a fake message.
ALTER TABLE approvals
  ALTER COLUMN message_id DROP NOT NULL;

-- Step 4: enforce the new shape going forward. Every row must have a subject.
ALTER TABLE approvals
  ALTER COLUMN subject_type SET NOT NULL,
  ALTER COLUMN subject_id   SET NOT NULL;

ALTER TABLE approvals
  DROP CONSTRAINT IF EXISTS approvals_subject_type_check;
ALTER TABLE approvals
  ADD  CONSTRAINT approvals_subject_type_check
       CHECK (subject_type IN ('message', 'autoflow_run'));

-- Step 5: belt-and-braces consistency for message subjects.
ALTER TABLE approvals
  DROP CONSTRAINT IF EXISTS approvals_subject_message_consistency;
ALTER TABLE approvals
  ADD  CONSTRAINT approvals_subject_message_consistency
       CHECK (
         (subject_type = 'message'      AND message_id IS NOT NULL AND subject_id = message_id)
         OR
         (subject_type = 'autoflow_run' AND message_id IS NULL)
       );

-- Step 6: swap the pending-uniqueness index to be subject-aware. The old
-- (message_id, approver_id) WHERE pending index becomes redundant for new
-- rows but harmless for legacy — Postgres just maintains it. Drop it to
-- avoid the dead-write cost.
DROP INDEX IF EXISTS uq_approvals_pending_per_message_approver;
CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_pending_per_subject_approver
  ON approvals(subject_type, subject_id, approver_id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_approvals_subject
  ON approvals(subject_type, subject_id);


-- ── RLS ──────────────────────────────────────────────────────────────────

ALTER TABLE autoflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE autoflow_runs ENABLE ROW LEVEL SECURITY;

-- Autoflows are admin-managed config. Members can read so the chat UI can
-- show "this answer triggered autoflow X"; only admins write.
DROP POLICY IF EXISTS autoflows_select ON autoflows;
CREATE POLICY autoflows_select ON autoflows
  FOR SELECT USING (org_id = auth_org_id());

-- Writes go through service-role (admin endpoints check role themselves).
-- Mirrors the pattern used by approvals (migration 028).

DROP POLICY IF EXISTS autoflow_runs_select ON autoflow_runs;
CREATE POLICY autoflow_runs_select ON autoflow_runs
  FOR SELECT USING (org_id = auth_org_id());


-- ── Triggers: keep updated_at fresh ──────────────────────────────────────

CREATE OR REPLACE FUNCTION autoflows_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_autoflows_updated_at ON autoflows;
CREATE TRIGGER trg_autoflows_updated_at
  BEFORE UPDATE ON autoflows
  FOR EACH ROW EXECUTE FUNCTION autoflows_set_updated_at();


-- ── Comments for future maintainers ──────────────────────────────────────

COMMENT ON TABLE autoflows IS
  'Agent2 Day 1: trigger/action automations. Inngest dispatchers in app/inngest/autoflow_functions.py.';

COMMENT ON COLUMN autoflows.trigger_config IS
  'Trigger-type-specific config. Scheduled: {cron: "0 9 * * 1"}. Others may carry filters: {collection_id, tags}.';

COMMENT ON COLUMN autoflows.actions IS
  'Ordered list of {type, config, order}. Validated by Pydantic AutoflowAction. See app/services/autoflow_actions.py for handlers.';

COMMENT ON COLUMN autoflows.last_fired_at IS
  'Dedupe key for the scheduled-cron dispatcher. Prevents double-fires across dispatcher restarts.';

COMMENT ON COLUMN approvals.subject_type IS
  'message | autoflow_run — distinguishes legacy per-message approvals from autoflow-gating approvals.';

COMMENT ON COLUMN autoflow_runs.blocking_approval_id IS
  'When status=held_for_approval, points to the approvals row that gates resume. Resume path: webhook approval.decided fires autoflow/resume.';
