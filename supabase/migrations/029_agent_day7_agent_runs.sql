-- ── Agent Roadmap Day 7 — Agent Run Infrastructure + Audit Trail
--
-- Every autonomous agent in Phase 2 (onboarding, policy propagation, support
-- response, weekly digest, …) logs into this table via BaseAgent. The table
-- is the single source of truth for the admin audit page at /admin/agent-runs.
--
-- Schema choices:
--   * agent_type and triggered_by are free-text (no CHECK) so new agents can
--     ship without a migration. The admin UI groups by these values.
--   * steps is a JSONB array of step records:
--       [{ step_name, status, result, error, timestamp }]
--     We append rather than overwrite so the audit is a true execution log.
--   * confidence_scores is a JSONB array — one entry per LLM call inside the
--     run. Lets the audit UI render a confidence-over-time mini-chart and
--     surface low-confidence runs for review.
--   * status enum gates the lifecycle transitions: only running → completed
--     / failed / cancelled / pending_approval are valid forward moves.

CREATE TABLE IF NOT EXISTS agent_runs (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- 'onboarding' | 'policy_propagation' | 'support_response' |
  -- 'knowledge_gap' | 'weekly_digest' | 'recruiting' | …  Free-text on
  -- purpose — new agents shouldn't need a schema migration.
  agent_type               TEXT NOT NULL,

  -- 'user' | 'webhook' | 'cron' | 'api'
  triggered_by             TEXT NOT NULL,
  triggered_by_user_id     UUID REFERENCES users(id) ON DELETE SET NULL,

  status                   TEXT NOT NULL DEFAULT 'running',
  input                    JSONB NOT NULL DEFAULT '{}'::jsonb,
  steps                    JSONB NOT NULL DEFAULT '[]'::jsonb,
  output                   JSONB NOT NULL DEFAULT '{}'::jsonb,
  error                    TEXT,

  llm_tokens_used          INT NOT NULL DEFAULT 0,
  confidence_scores        JSONB NOT NULL DEFAULT '[]'::jsonb,

  -- For agents that gate execution on an approval row, we cross-link so the
  -- audit UI can navigate to the approval and back. Nullable — most agents
  -- run autonomously.
  approval_id              UUID REFERENCES approvals(id) ON DELETE SET NULL,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at               TIMESTAMPTZ,
  completed_at             TIMESTAMPTZ,

  CONSTRAINT agent_runs_status_check
    CHECK (status IN ('running', 'completed', 'failed', 'cancelled', 'pending_approval'))
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_org_created
  ON agent_runs(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_runs_org_type_created
  ON agent_runs(org_id, agent_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_runs_org_status_created
  ON agent_runs(org_id, status, created_at DESC);


ALTER TABLE agent_runs ENABLE ROW LEVEL SECURITY;

-- Admin-only audit visibility. Agent runs can contain sensitive content
-- (drafted emails, hire details) so we restrict to admins like the
-- knowledge_gaps + moderation tables.
DROP POLICY IF EXISTS agent_runs_select_admin ON agent_runs;
CREATE POLICY agent_runs_select_admin ON agent_runs
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Writes via service-role only (agents run inside Inngest workers).

COMMENT ON TABLE agent_runs IS
  'Day 7: Audit trail + step log for every autonomous agent. BaseAgent writes here.';
