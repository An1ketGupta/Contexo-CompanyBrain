-- ── 057 — Proactive Morning Briefings (Feature 2.2)
--
-- Two tables:
--   1. briefing_preferences  — per-user opt-in + schedule + topic preferences.
--   2. briefings             — one row per generated briefing (history,
--                              re-fetch from the in-app notification, audit).
--
-- The weekly Inngest cron (Monday at 08:00 in the org's timezone) walks
-- `briefing_preferences WHERE enabled = true`, composes a briefing for each
-- user, and writes a row here + dispatches email + notification.

-- ── briefing_preferences ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS briefing_preferences (
  user_id      UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- Master switch. Default true for new users so the feature is discoverable
  -- via the Monday email — they can opt out from the briefing itself or
  -- from settings. If we wanted opt-in only we'd flip this to false.
  enabled      BOOLEAN NOT NULL DEFAULT true,
  -- 'weekly' for now. Reserved for 'daily' later.
  frequency    TEXT NOT NULL DEFAULT 'weekly'
    CHECK (frequency IN ('weekly')),
  -- Day-of-week 0..6 where 0=Sunday (matches Postgres EXTRACT(DOW)).
  weekday      INTEGER NOT NULL DEFAULT 1
    CHECK (weekday BETWEEN 0 AND 6),
  -- Hour-of-day in the user's timezone (0..23).
  hour         INTEGER NOT NULL DEFAULT 8
    CHECK (hour BETWEEN 0 AND 23),
  -- IANA tz string. Defaults to UTC; the user picks one in settings.
  timezone     TEXT NOT NULL DEFAULT 'UTC',
  -- JSONB array of topic strings the user wants over-weighted in the
  -- "trending topics" section. e.g. ["Q3 launch", "competitor"].
  topics       JSONB NOT NULL DEFAULT '[]',
  -- Channels the user wants notified. Booleans rather than a single mask
  -- so we can add Slack later without a schema migration.
  via_email    BOOLEAN NOT NULL DEFAULT true,
  via_inapp    BOOLEAN NOT NULL DEFAULT true,
  -- Bookkeeping. last_sent_at lets the cron skip a user it already served
  -- this week even on a delayed retry.
  last_sent_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_briefing_prefs_org
  ON briefing_preferences (org_id) WHERE enabled = true;

CREATE OR REPLACE FUNCTION briefing_prefs_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_briefing_prefs_touch_updated_at ON briefing_preferences;
CREATE TRIGGER trg_briefing_prefs_touch_updated_at
  BEFORE UPDATE ON briefing_preferences
  FOR EACH ROW EXECUTE FUNCTION briefing_prefs_touch_updated_at();

ALTER TABLE briefing_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "briefing_prefs_self_all" ON briefing_preferences;
CREATE POLICY "briefing_prefs_self_all" ON briefing_preferences
  FOR ALL USING (org_id = auth_org_id() AND user_id = auth.uid())
  WITH CHECK (org_id = auth_org_id() AND user_id = auth.uid());


-- ── briefings ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS briefings (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status        TEXT NOT NULL DEFAULT 'generating'
    CHECK (status IN ('generating', 'ok', 'failed')),
  error_message TEXT,
  -- Final prose body (markdown). Kept separate from `data` JSONB so the
  -- email renderer can stream from one column.
  body_md       TEXT,
  -- Compact summary used as the in-app notification body and email preview.
  summary       TEXT,
  -- The structured payload that fed the LLM: top 3 knowledge gaps, stale
  -- docs, meetings, trending topics. Useful for debugging and for a future
  -- "show data behind this briefing" UI.
  data          JSONB NOT NULL DEFAULT '{}',
  -- ISO week (e.g. "2026-W26"). Used as the dedupe_key for the
  -- notification AND as a UNIQUE bound so a retry can't double-write the
  -- same user's week.
  period_key    TEXT NOT NULL,
  delivered_email_at  TIMESTAMPTZ,
  delivered_inapp_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (user_id, period_key)
);

CREATE INDEX IF NOT EXISTS idx_briefings_user_recent
  ON briefings (user_id, created_at DESC);

ALTER TABLE briefings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "briefings_self_select" ON briefings;
CREATE POLICY "briefings_self_select" ON briefings
  FOR SELECT USING (org_id = auth_org_id() AND user_id = auth.uid());

-- No write policy — the worker uses service-role.

COMMENT ON TABLE briefings IS
  'Feature 2.2: one row per generated proactive briefing. Read by /briefings page and the in-app notification deep-link. Written exclusively by the weekly Inngest worker via service-role.';
