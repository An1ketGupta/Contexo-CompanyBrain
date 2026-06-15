-- ── Agent Roadmap Day 14 — API-triggered approvals + version diff broadening
--
-- Extends `approvals` (Day 6) so external systems calling the public API can
-- route an agent run through a human approver before it executes. The
-- existing magic-link / Slack-button / web flows continue to work — we just
-- broaden the schema:
--
--   * `message_id` becomes NULL-able. API-triggered approvals don't have a
--     parent chat message; they reference an agent_type + input instead.
--   * `requested_by` becomes NULL-able. The trigger is the API key, not a
--     workspace user. We log the key id in `audit` so the inbox can show
--     "Requested by API key (Production server)".
--   * Three new columns capture the agent context:
--       agent_type    — onboarding | policy_propagation | support_response | weekly_digest | …
--       agent_input   — JSONB of the validated trigger payload
--       agent_run_id  — set once the approval is approved and the agent run starts
--     These ride alongside `execution_action` because we still want the
--     execution-action validator to fire ("channel": "agent" is a new value).
--   * `api_key_id` records which key initiated the request — auditable.
--
-- We KEEP the not-self constraint as `requested_by IS NULL OR requested_by <> approver_id`
-- so API approvals are fine and human approvals still prevent self-approval.
--
-- A new CHECK guarantees every row has either a message_id OR an agent_type
-- but not neither — the surface that matters is "what is being approved."

ALTER TABLE approvals
  ALTER COLUMN message_id DROP NOT NULL,
  ALTER COLUMN requested_by DROP NOT NULL;

ALTER TABLE approvals
  ADD COLUMN IF NOT EXISTS agent_type    TEXT,
  ADD COLUMN IF NOT EXISTS agent_input   JSONB,
  ADD COLUMN IF NOT EXISTS agent_run_id  UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS api_key_id    UUID REFERENCES api_keys(id) ON DELETE SET NULL;

-- Drop the legacy NOT-NULL-implied not-self constraint and replace with a
-- nullable-tolerant version. Existing rows are unaffected.
ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_not_self;
ALTER TABLE approvals
  ADD CONSTRAINT approvals_not_self
  CHECK (requested_by IS NULL OR requested_by <> approver_id);

-- Either a chat message OR an agent context — never both empty.
ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_target_present;
ALTER TABLE approvals
  ADD CONSTRAINT approvals_target_present
  CHECK (message_id IS NOT NULL OR agent_type IS NOT NULL);

-- The Day 6 unique index keyed by message_id needs a sibling for agent
-- approvals: one pending approval per (agent_type, approver, key hash on
-- input). We hash on input JSONB to make the index predicate stable.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_pending_per_agent_approver
  ON approvals(agent_type, approver_id, md5(agent_input::text))
  WHERE status = 'pending' AND agent_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_approvals_org_agent_type_status
  ON approvals(org_id, agent_type, status, created_at DESC)
  WHERE agent_type IS NOT NULL;

COMMENT ON COLUMN approvals.agent_type IS
  'Day 14: set when an approval gates an API-triggered agent run instead of a chat message.';
COMMENT ON COLUMN approvals.agent_input IS
  'Day 14: validated trigger payload. Shape mirrors AGENT_INPUT_SCHEMAS in the public API.';
COMMENT ON COLUMN approvals.agent_run_id IS
  'Day 14: agent_runs.id once the approval is approved and the agent fires.';
COMMENT ON COLUMN approvals.api_key_id IS
  'Day 14: which api_keys row triggered this approval. Surfaces in the inbox audit line.';


-- ── Outbound webhook events for agent lifecycle ──────────────────────────
-- Two new event names join the allowlist enforced in services/webhooks.py.
-- We don't store the allowlist in SQL; this comment documents the contract.
--   agent.completed  — fired when ANY agent_runs row transitions to completed
--   agent.failed     — fired when ANY agent_runs row transitions to failed
