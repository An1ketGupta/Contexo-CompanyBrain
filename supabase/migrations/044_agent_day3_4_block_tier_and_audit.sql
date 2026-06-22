-- ── Agent Roadmap Days 3-4 — Production hardening
--
-- Closes three gaps that the existing Day 3-4 code stopped short of:
--   1) Confidence "block" tier — a third cosine cutoff below which outbound
--      writes (Slack post / Gmail send / Notion create / Gdocs export) refuse
--      to publish. The high/medium tiers live in
--      organizations.metadata.confidence_thresholds; we just extend the same
--      JSONB key with a `block` field. Default 0.0 = off (preserves existing
--      org behavior — admins opt in via the settings page).
--
--   2) `outbound_write_*` agent_runs audit. No schema change needed —
--      agent_type is free-text by design (029). This migration just adds a
--      partial index so /admin/agent-runs can filter by the outbound-write
--      family without scanning the whole table.
--
--   3) Two new webhook events (output.delivered, output.failed). Recorded
--      in the ALLOWED_EVENTS docstring; nothing here to migrate (events are
--      a JSONB array on webhooks rows), but adding a CHECK list would be
--      counter-productive — we keep webhook event validation in app code.
--
-- This migration is forward-only and idempotent. No backfill required.

-- (1) Backfill default block = null on any existing org so the JSONB key
--     exists but is treated as "off". Optional — the reader in
--     org_config.py defaults to 0.0 when the key is missing.
--
--     We deliberately do NOT add a CHECK constraint here. The validation
--     lives in update_confidence_thresholds() and the Pydantic body — and
--     a hand-edited row should fail open (default values) rather than
--     break every chat call with a constraint error.

-- (2) Targeted index for the audit UI's "outbound writes" tab. Uses a
--     LIKE prefix filter because we have four agent_type values:
--     outbound_write_slack, _gmail, _notion, _gdocs.
CREATE INDEX IF NOT EXISTS idx_agent_runs_org_outbound_created
  ON agent_runs(org_id, created_at DESC)
  WHERE agent_type LIKE 'outbound_write_%';

-- (3) Rate-limit + dedup keys land in Upstash, not Postgres. No table needed.

COMMENT ON INDEX idx_agent_runs_org_outbound_created IS
  'Day 3-4 hardening: filter outbound_write_* agent_runs for the admin audit feed.';
