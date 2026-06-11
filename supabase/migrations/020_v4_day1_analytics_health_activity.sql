-- ── V4 Day 1: analytics events, document health, activity feed ────────────
--
-- Three independent additions, grouped because they ship together:
--   1. analytics_events       — generic product-event log (fire-and-forget)
--   2. documents.health_*     — recency/freshness scoring (nightly cron)
--   3. activity_feed          — team-visible "Aniket generated content" feed
--   4. users.activity_private — per-user opt-out of #3
--
-- All writes happen via the service role (analytics is telemetry, not user
-- state — we can't fail a user request because a log insert failed). RLS on
-- read is enforced for both new tables so org_id leakage is impossible.

-- ── 1. analytics_events ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analytics_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- NULLable so we can attribute system-generated events (digest sends, cron
  -- runs) to "the system" rather than fake a user_id.
  user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
  event_type  TEXT NOT NULL CHECK (char_length(event_type) BETWEEN 1 AND 100),
  -- Free-form. Conventions live in app/services/analytics.py; the DB only
  -- promises it's a JSON object. NEVER store PII here — query text is logged
  -- as length, not content.
  metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Time-series read pattern: filter by org, optionally by type, sort desc.
CREATE INDEX IF NOT EXISTS idx_analytics_events_org_time
  ON analytics_events (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_org_type_time
  ON analytics_events (org_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_user_time
  ON analytics_events (user_id, created_at DESC);

ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY;

-- Org members can read their own org's events. Insert is service-role only
-- (no INSERT policy → PostgREST forbids it for authenticated callers).
DROP POLICY IF EXISTS analytics_events_select ON analytics_events;
CREATE POLICY analytics_events_select ON analytics_events
  FOR SELECT
  USING (org_id = auth_org_id());

-- ── 2. Document health columns ─────────────────────────────────────────────
-- All four are nullable: a brand-new doc has no health computed yet, and the
-- nightly cron fills them in. Frontend renders "Unscored" when health_label
-- is NULL.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS last_accessed_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS health_score        REAL CHECK (health_score IS NULL OR (health_score >= 0 AND health_score <= 1)),
  ADD COLUMN IF NOT EXISTS health_label        TEXT CHECK (health_label IS NULL OR health_label IN ('healthy','stale','at_risk','unused')),
  ADD COLUMN IF NOT EXISTS health_computed_at  TIMESTAMPTZ,
  -- V3 knowledge-gap detection increments this when a query couldn't find
  -- a good answer despite this doc being the closest hit.
  ADD COLUMN IF NOT EXISTS gap_flag_count      INTEGER NOT NULL DEFAULT 0;

-- Sort index for the admin "at-risk first" view.
CREATE INDEX IF NOT EXISTS idx_documents_health_label
  ON documents (org_id, health_label, health_score)
  WHERE status = 'ready';

-- Cheap bump: cron query needs all ready docs ordered by health_computed_at
-- ascending (oldest scores first), so we can pace the recompute if it ever
-- grows past one-batch.
CREATE INDEX IF NOT EXISTS idx_documents_health_recompute
  ON documents (org_id, health_computed_at NULLS FIRST)
  WHERE status = 'ready';

-- ── 3. activity_feed ───────────────────────────────────────────────────────
-- Distinct from analytics_events because:
--   * activity_feed is user-facing → has a `summary` ready to render
--   * has an is_private toggle (per #4) tied to the user's preference
--   * never logs system events — every row has a user_id NOT NULL
CREATE TABLE IF NOT EXISTS activity_feed (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  activity_type TEXT NOT NULL CHECK (activity_type IN (
    'generated_content', 'uploaded_doc', 'shared_output', 'used_template'
  )),
  -- Rendered server-side at write time using ACTIVITY_TEMPLATES so the feed
  -- doesn't need a separate i18n/format pass on read.
  summary       TEXT NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 280),
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Materialised from users.activity_private AT WRITE TIME — flipping the
  -- toggle does not retroactively hide old activity. This is intentional:
  -- "going private" is a forward decision, like Slack's DM history settings.
  is_private    BOOLEAN NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activity_feed_org_visible
  ON activity_feed (org_id, created_at DESC)
  WHERE is_private = false;
CREATE INDEX IF NOT EXISTS idx_activity_feed_user
  ON activity_feed (user_id, created_at DESC);

ALTER TABLE activity_feed ENABLE ROW LEVEL SECURITY;

-- Team-visible rows readable by any org member. Private rows readable only by
-- the actor themselves. No INSERT/UPDATE/DELETE policies — service role only.
DROP POLICY IF EXISTS activity_feed_select ON activity_feed;
CREATE POLICY activity_feed_select ON activity_feed
  FOR SELECT
  USING (
    org_id = auth_org_id()
    AND (is_private = false OR user_id = auth.uid())
  );

-- ── 4. Users: activity privacy toggle ──────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS activity_private BOOLEAN NOT NULL DEFAULT false;
