-- ── Migration 036: in-app notifications ────────────────────────────────────
-- General-purpose per-user notification feed. First consumer is the Monday
-- review-reminder cron (V2 Day 13 / #38); future consumers include knowledge
-- gaps, compliance acks, approval queues, etc.
--
-- Design:
--   * One row per (user_id, event). Cron de-duplicates via dedupe_key so a
--     re-fired Monday cron doesn't double-up the same week's reminder.
--   * read_at NULL = unread; setting it stamps the read time. Soft delete
--     happens via a 90-day retention cleanup (separate cron, not in scope).
--   * INSERTs come from service-role only (background functions). Users can
--     SELECT/UPDATE their own rows via the user-scoped client.

CREATE TABLE IF NOT EXISTS notifications (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- Free-form discriminator. Keep it lowercase-snake (e.g. 'review_reminder',
  -- 'knowledge_gap', 'approval_request'). No enum so we can add types without
  -- a migration.
  type        TEXT NOT NULL,
  title       TEXT NOT NULL,
  -- Optional secondary line. Templates render `title` always, `body` when set.
  body        TEXT,
  -- Type-specific payload (doc ids, counts, etc.). Templates inspect this.
  metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
  -- Where to send the user when they click. Relative URL preferred.
  link_url    TEXT,
  -- Dedupe across re-fired crons / retries. Combined with type below in a
  -- partial unique index so the bg writer can use INSERT … ON CONFLICT.
  dedupe_key  TEXT,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Main list query: latest N for a user, optionally filtered to unread.
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
  ON notifications (user_id, created_at DESC);

-- Unread-count query path. Partial on read_at IS NULL keeps the index tiny
-- because most rows are read-once-then-forgotten.
CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
  ON notifications (user_id)
  WHERE read_at IS NULL;

-- Idempotency: cron writes carry (type, dedupe_key) and rely on this to
-- silently skip duplicates. NULL dedupe_key rows are allowed unlimited
-- duplicates (e.g. instant alerts that intentionally re-fire).
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_dedupe
  ON notifications (user_id, type, dedupe_key)
  WHERE dedupe_key IS NOT NULL;

ALTER TABLE notifications ENABLE ROW LEVEL SECURITY;

-- Users see their own notifications. No org-wide broadcast for now —
-- if we ever need that, add an OR clause matching user_id IS NULL with org_id.
CREATE POLICY "notifications: read own"
  ON notifications FOR SELECT
  USING (user_id = auth.uid());

-- Users mark their own as read. The UPDATE policy gates which rows can be
-- touched; the CHECK gates which fields can be changed (only read_at).
CREATE POLICY "notifications: mark own read"
  ON notifications FOR UPDATE
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

-- No INSERT/DELETE policy = blocked for non-service-role roles. Background
-- functions use the service-role client which bypasses RLS by design.
