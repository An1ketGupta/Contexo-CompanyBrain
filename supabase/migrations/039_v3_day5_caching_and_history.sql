-- ── V3 Day 5: Query history + recommended documents ──────────────────────
--
-- Two additions for the V3 Day 5 push:
--
--  1. `query_logs` — every chat turn writes one row, fire-and-forget from
--     the orchestrator. Powers the user-facing /history page and (later)
--     analytics slicing by intent. Distinct from `analytics_events` which
--     is a wide event log; query_logs is a narrow, indexed, user-queryable
--     view of "what did I ask?" with deep-links back to the conversation.
--
--  2. `organizations.recommended_documents` JSONB — populated post-enrichment
--     from a use-case→template map. Each entry tracks match state so the
--     widget shows a checklist with progress and the upload handler can
--     auto-tick matches via fuzzy name comparison.
--
-- Both are additive — no existing reads break.

-- ── 1. query_logs ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS query_logs (
  id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID        NOT NULL REFERENCES users(id)         ON DELETE CASCADE,
  org_id          UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  conversation_id UUID                 REFERENCES conversations(id) ON DELETE SET NULL,
  message_id      UUID                 REFERENCES messages(id)      ON DELETE SET NULL,
  -- Truncated to 500 chars at the writer; the DB cap is the defense-in-depth.
  query_text      TEXT        NOT NULL CHECK (char_length(query_text) <= 1000),
  -- intent label from the intent classifier (factual_qa, task_generation, …)
  -- Nullable because the classifier is best-effort and can return None.
  intent          VARCHAR(50),
  -- Response shape — useful for analytics on "what kinds of answers do we
  -- actually produce?" and quick filtering ("show me searches with no sources").
  response_length INTEGER     NOT NULL DEFAULT 0 CHECK (response_length >= 0),
  source_count    INTEGER     NOT NULL DEFAULT 0 CHECK (source_count >= 0),
  tool_calls      INTEGER     NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
  latency_ms      INTEGER              CHECK (latency_ms IS NULL OR latency_ms >= 0),
  model_used      VARCHAR(50),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot path: /me/query-history scoped to user, newest-first. Cursor-paginated
-- by (created_at, id) so we get stable ordering without OFFSET pagination.
CREATE INDEX IF NOT EXISTS idx_query_logs_user_created
  ON query_logs (user_id, created_at DESC, id DESC);

-- Org-level scan for admin analytics + retention cron.
CREATE INDEX IF NOT EXISTS idx_query_logs_org_created
  ON query_logs (org_id, created_at DESC);

-- Filter-by-intent on the history page (subset of the user_created index but
-- with intent as the leading column for "show me only writing-mode queries").
CREATE INDEX IF NOT EXISTS idx_query_logs_user_intent
  ON query_logs (user_id, intent, created_at DESC)
  WHERE intent IS NOT NULL;

-- Retention cron filter — every row past the cutoff. BTree on created_at is
-- fine since the cron does a single DELETE ... WHERE created_at < cutoff.

ALTER TABLE query_logs ENABLE ROW LEVEL SECURITY;

-- Users only ever read their own query_logs from the API. The service role
-- (used by the writer in task_chain and the retention cron) bypasses RLS,
-- so we don't need a policy for it.
DROP POLICY IF EXISTS query_logs_select_own ON query_logs;
CREATE POLICY query_logs_select_own
  ON query_logs
  FOR SELECT
  USING (user_id = auth.uid());

-- No INSERT/UPDATE/DELETE policies — writes happen via service role from the
-- orchestrator. Locking writes out from the user JWT prevents log forgery.

-- ── 2. organizations.recommended_documents ────────────────────────────────
-- Shape: JSONB array of
--   { "key": "employee_handbook",
--     "name": "Employee Handbook",
--     "description": "…",
--     "why": "…",
--     "examples": ["…", "…"],
--     "matched_document_id": UUID | null,
--     "matched_at": ISO8601 | null,
--     "dismissed_at": ISO8601 | null }
--
-- Stored on `organizations` rather than `organizations.metadata` so we get a
-- typed-ish column (JSONB) the widget can read with one selector and we can
-- easily add a partial GIN index later if we need to query inside it.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS recommended_documents JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Sanity check: must be an array. JSONB lets you stuff a string in if you're
-- not careful; the constraint catches that before it confuses the frontend.
ALTER TABLE organizations
  DROP CONSTRAINT IF EXISTS organizations_recommended_documents_is_array;
ALTER TABLE organizations
  ADD  CONSTRAINT organizations_recommended_documents_is_array
  CHECK (jsonb_typeof(recommended_documents) = 'array');

COMMENT ON TABLE  query_logs IS
  'V3 Day 5 #91. One row per chat turn for user-facing query history. '
  'Distinct from analytics_events (which is the wide event log).';
COMMENT ON COLUMN organizations.recommended_documents IS
  'V3 Day 5 #50. Use-case-driven document checklist populated post-enrichment. '
  'Each entry tracks match state for the Documents-page widget.';
