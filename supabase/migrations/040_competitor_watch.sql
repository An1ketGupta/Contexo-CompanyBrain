-- ── 040 — Competitor watch ──────────────────────────────────────────────────
--
-- Two watchlists + one mentions log:
--
--   * organizations.competitor_names — admin-managed list applied to everyone
--     in the workspace (e.g. "Acme Inc", "Globex"). Detection fires when the
--     assistant output mentions any of these — even if the user wasn't watching.
--
--   * users.competitor_names — per-user personal watchlist on top of the org
--     list (e.g. an SDR tracking a specific accounts). Doesn't flag anyone
--     else's chats; only the user's own outputs are scanned against it.
--
--   * competitor_mentions — one row per (output × matched term × watchlist).
--     Stored at term grain so the admin page can group/filter/dismiss by
--     term cheaply, and a single message hitting three terms shows three
--     distinct entries. Snippet captured at write time so the row is
--     useful even after the source message/agent_run row is deleted.
--
-- Detection is read-only on the LLM side: regex matching only, no model
-- calls. Persistence happens after the assistant message / agent_run row
-- is committed, so foreign keys are always valid.

-- ── Watchlists ──────────────────────────────────────────────────────────────

ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS competitor_names TEXT[] NOT NULL DEFAULT '{}'::TEXT[];

ALTER TABLE organizations
  ADD CONSTRAINT organizations_competitor_names_len
  CHECK (array_length(competitor_names, 1) IS NULL OR array_length(competitor_names, 1) <= 200);

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS competitor_names TEXT[] NOT NULL DEFAULT '{}'::TEXT[];

ALTER TABLE users
  ADD CONSTRAINT users_competitor_names_len
  CHECK (array_length(competitor_names, 1) IS NULL OR array_length(competitor_names, 1) <= 100);

-- ── competitor_mentions ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS competitor_mentions (
  id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  -- 'chat' rows attach to a message; 'agent' rows attach to an agent_runs row.
  -- Exactly one of message_id / agent_run_id must be non-null.
  source_kind       TEXT        NOT NULL CHECK (source_kind IN ('chat', 'agent')),
  message_id        UUID                 REFERENCES messages(id)      ON DELETE CASCADE,
  agent_run_id      UUID                 REFERENCES agent_runs(id)    ON DELETE CASCADE,
  conversation_id   UUID                 REFERENCES conversations(id) ON DELETE SET NULL,
  -- The actor who produced the output. Nullable for system-triggered agent runs.
  user_id           UUID                 REFERENCES users(id)         ON DELETE SET NULL,
  -- The literal watchlist string that fired (case preserved from the list).
  matched_term      TEXT        NOT NULL CHECK (char_length(matched_term) BETWEEN 1 AND 200),
  -- Which list contributed this term — drives "org list" vs "your watchlist"
  -- labels in the admin page and the inline banner.
  watchlist_source  TEXT        NOT NULL CHECK (watchlist_source IN ('org', 'user')),
  -- ±100 chars of context around the first hit in the output, stored at
  -- write time so the row survives source deletion.
  snippet           TEXT        NOT NULL CHECK (char_length(snippet) <= 600),
  -- Occurrence count for this term in the output. Cheap to maintain and
  -- useful for ranking "how often Acme came up" in the admin grid.
  match_count       INT         NOT NULL DEFAULT 1 CHECK (match_count > 0),
  status            TEXT        NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'dismissed')),
  dismissed_by      UUID                 REFERENCES users(id) ON DELETE SET NULL,
  dismissed_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT competitor_mentions_one_source
    CHECK (
      (source_kind = 'chat'  AND message_id  IS NOT NULL AND agent_run_id IS NULL)
      OR
      (source_kind = 'agent' AND agent_run_id IS NOT NULL AND message_id  IS NULL)
    ),
  CONSTRAINT competitor_mentions_dismiss_audit
    CHECK (
      (status = 'open'      AND dismissed_at IS NULL AND dismissed_by IS NULL)
      OR
      (status = 'dismissed' AND dismissed_at IS NOT NULL)
    )
);

-- Admin feed: org-scoped newest-first, optionally filtered by status.
CREATE INDEX IF NOT EXISTS idx_competitor_mentions_org_created
  ON competitor_mentions (org_id, created_at DESC);

-- "Show me all open mentions of Acme" — covers the bulk-dismiss-by-term path
-- and the grouped view.
CREATE INDEX IF NOT EXISTS idx_competitor_mentions_org_term
  ON competitor_mentions (org_id, matched_term, status, created_at DESC);

-- Inline banner lookup: "are there mentions on this assistant message?".
CREATE UNIQUE INDEX IF NOT EXISTS uq_competitor_mentions_message_term
  ON competitor_mentions (message_id, matched_term, watchlist_source)
  WHERE message_id IS NOT NULL;

-- Same for agent runs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_competitor_mentions_agent_run_term
  ON competitor_mentions (agent_run_id, matched_term, watchlist_source)
  WHERE agent_run_id IS NOT NULL;

ALTER TABLE competitor_mentions ENABLE ROW LEVEL SECURITY;

-- All org members can read mentions in their org (so the inline banner works
-- without exposing the message author's identity). Admin gating for the
-- review page lives in the API layer — RLS protects the data perimeter, not
-- the UI affordance.
CREATE POLICY competitor_mentions_select_org
  ON competitor_mentions FOR SELECT
  USING (
    org_id IN (SELECT u.org_id FROM users u WHERE u.id = auth.uid())
  );

-- Only admins can mark mentions dismissed via the user-scoped client.
-- (Service role bypasses RLS for inserts from the orchestrator.)
CREATE POLICY competitor_mentions_update_admin
  ON competitor_mentions FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM users u
      WHERE u.id = auth.uid()
        AND u.org_id = competitor_mentions.org_id
        AND u.role = 'admin'
    )
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM users u
      WHERE u.id = auth.uid()
        AND u.org_id = competitor_mentions.org_id
        AND u.role = 'admin'
    )
  );

COMMENT ON TABLE  competitor_mentions IS
  'One row per (assistant output × matched competitor term × watchlist) flagged by the post-generation detector.';
COMMENT ON COLUMN organizations.competitor_names IS
  'Workspace-wide competitor watchlist. Admin-managed. Up to 200 entries.';
COMMENT ON COLUMN users.competitor_names IS
  'Personal competitor watchlist layered on top of the org list. Up to 100 entries.';
