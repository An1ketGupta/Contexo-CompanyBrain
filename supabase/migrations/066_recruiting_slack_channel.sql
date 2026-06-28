-- Recruiting: default Slack channel for requisition announcements.
--
-- Mirrors 065 (Notion default parent): per-org default chosen once during
-- recruiting onboarding, with a per-requisition override at publish time.
-- Stored on slack_integrations so it cascades when the org disconnects Slack.
--
-- We persist the channel name alongside the id so the picker chip can render
-- "#hiring-feed" without re-hitting Slack on every page render. The name may
-- drift when a workspace renames a channel — that's acceptable; the org
-- defaults endpoint refreshes the cached name whenever the user opens the
-- picker.
ALTER TABLE slack_integrations
  ADD COLUMN IF NOT EXISTS default_recruiting_slack_channel_id TEXT,
  ADD COLUMN IF NOT EXISTS default_recruiting_slack_channel_name TEXT;
