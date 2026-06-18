-- ── Migration 037: Slack inbound (pins + canvases + files) ──────────────────
-- Adds the bookkeeping needed to ingest content FROM Slack (we already post
-- TO Slack via the v5 outbound flow). Two pieces:
--
--   1. slack_integrations.scopes  — track which OAuth scopes the bot actually
--      received. The bot starts with just chat:write,commands; inbound needs
--      pins:read, channels:history, channels:read, files:read, users:read,
--      groups:read, groups:history, canvases:read. The UI shows a "Reconnect
--      to enable inbound" banner when scopes_complete is false.
--
--   2. slack_channel_subscriptions  — explicit allowlist of channels the org
--      wants ingested. Admin-only. One row per (org, channel). The bot must
--      already be a member of the channel (Slack enforces this server-side).
--
-- Document representation lives in the existing `documents` table with
-- source='slack' and external_id='slack:<team>:<channel>:<ts>' (or
-- 'slack:<team>:canvas:<id>' / 'slack:<team>:file:<id>'). No schema change
-- needed on documents.

-- ── 1. scopes column ────────────────────────────────────────────────────────
ALTER TABLE slack_integrations
  ADD COLUMN IF NOT EXISTS scopes TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN slack_integrations.scopes IS
  'OAuth scopes the bot actually received on the latest install. Compared
   against the required-for-inbound set to decide if a reconnect banner shows.';


-- ── 2. slack_channel_subscriptions ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS slack_channel_subscriptions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  channel_id          TEXT NOT NULL,                 -- "C012ABC..." or "G..." for private
  channel_name        TEXT,                          -- best-effort display name
  is_private          BOOLEAN NOT NULL DEFAULT false,
  -- Per-channel ingest toggles. Defaults match the v1 product decision:
  -- pins + canvases + files on; arbitrary channel messages off (privacy).
  ingest_pins         BOOLEAN NOT NULL DEFAULT true,
  ingest_canvases     BOOLEAN NOT NULL DEFAULT true,
  ingest_files        BOOLEAN NOT NULL DEFAULT true,
  ingest_messages     BOOLEAN NOT NULL DEFAULT false,
  -- Last successful backfill timestamp. NULL = never backfilled (the next
  -- backfill job will do a full one-shot pull).
  last_backfilled_at  TIMESTAMPTZ,
  -- If the bot loses access (kicked, channel archived, scope revoked) we set
  -- a non-null status here so the UI can surface "needs attention".
  last_error          TEXT,
  last_error_at       TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_slack_subscriptions_org
  ON slack_channel_subscriptions (org_id);
CREATE INDEX IF NOT EXISTS idx_slack_subscriptions_channel
  ON slack_channel_subscriptions (channel_id);

ALTER TABLE slack_channel_subscriptions ENABLE ROW LEVEL SECURITY;

-- Same access pattern as slack_integrations: admin-only. Members can see what
-- channels are ingested by inferring it from sources on chat answers; they
-- don't need to manage the list.
CREATE POLICY "slack_channel_subscriptions_select_admin"
  ON slack_channel_subscriptions FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

CREATE POLICY "slack_channel_subscriptions_modify_admin"
  ON slack_channel_subscriptions FOR ALL USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );
