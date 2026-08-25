-- Remove the deferred external developer platform: REST API keys, remote MCP,
-- and API-triggered agent approvals. Internal application routes and agents
-- remain unchanged.

-- These approval rows can only have been created by the retired public API.
-- Deleting them also clears agent_runs.approval_id through ON DELETE SET NULL.
DELETE FROM approvals
WHERE message_id IS NULL OR requested_by IS NULL;

DROP INDEX IF EXISTS uq_approvals_pending_per_agent_approver;
DROP INDEX IF EXISTS idx_approvals_org_agent_type_status;

ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_target_present;
ALTER TABLE approvals
  DROP COLUMN IF EXISTS agent_type,
  DROP COLUMN IF EXISTS agent_input,
  DROP COLUMN IF EXISTS agent_run_id,
  DROP COLUMN IF EXISTS api_key_id,
  ALTER COLUMN message_id SET NOT NULL,
  ALTER COLUMN requested_by SET NOT NULL;

-- MCP audit rows depend on API keys, so remove them first.
DROP TABLE IF EXISTS mcp_tool_invocations;
DROP TABLE IF EXISTS api_keys;
