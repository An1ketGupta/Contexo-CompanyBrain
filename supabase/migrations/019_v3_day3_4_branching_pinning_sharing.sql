-- ── V3 Day 3 + Day 4: pinned conversations, message branching, shareable links ──
--
-- Day 3 needs:
--   • conversations.is_pinned for sidebar "starred" ordering (#30).
--   • messages branching columns so a single user turn can produce several
--     parallel assistant answers (#42). We chose `parent_user_message_id`
--     over the roadmap's `parent_message_id` so all siblings of a regen group
--     share one parent, which collapses "count branches" to a single
--     equality query and avoids special-casing the original.
--
-- Day 4 needs:
--   • organizations.allow_output_sharing — admin kill-switch for #62.
--   • shared_outputs table — one row per message-share, with a URL-safe
--     token and a view counter. RLS lets org members manage their own
--     shares; the *public* anon GET goes through the service-role client
--     in the FastAPI sharing router (RLS would block it).

-- ── 1. Pinned conversations ───────────────────────────────────────────────
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT false;

-- Composite index that matches our sidebar ORDER BY: pinned-first then
-- recency. Equality on (user_id, is_pinned) before the range-style
-- updated_at DESC is the order Postgres can actually use end-to-end.
CREATE INDEX IF NOT EXISTS idx_conversations_user_pinned_updated
  ON conversations (user_id, is_pinned DESC, updated_at DESC);

-- ── 2. Message branching ──────────────────────────────────────────────────
-- parent_user_message_id is the user turn that spawned this assistant branch.
-- All siblings of a regen group share the same parent_user_message_id, so:
--   * `SELECT count(*) FROM messages WHERE parent_user_message_id = ?`
--     is the branch count.
--   * Reconstructing the timeline is one pass: emit user rows + the one
--     assistant row per parent_user_message_id where is_active_branch=true.
-- The original assistant turn lives in the same shape (branch_index=0,
-- is_active_branch=true), so the loader doesn't need a special case.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS parent_user_message_id UUID
    REFERENCES messages(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS branch_index INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS is_active_branch BOOLEAN NOT NULL DEFAULT true;

-- Backfill: every existing assistant message becomes branch 0 of the user
-- message immediately preceding it. We do this with a window function so
-- the pairing is deterministic per conversation regardless of clock skew.
WITH paired AS (
  SELECT
    m.id            AS assistant_id,
    LAG(m.id) OVER (PARTITION BY m.conversation_id ORDER BY m.created_at) AS prev_id,
    LAG(m.role) OVER (PARTITION BY m.conversation_id ORDER BY m.created_at) AS prev_role
  FROM messages m
  WHERE m.role = 'assistant'
)
UPDATE messages msg
SET parent_user_message_id = paired.prev_id
FROM paired
WHERE msg.id = paired.assistant_id
  AND paired.prev_role = 'user'
  AND msg.parent_user_message_id IS NULL;

-- One active branch per group. Partial unique index — only enforced on
-- the active row, so flipping branches via PATCH is just two UPDATEs in a
-- single transaction (mark old false, mark new true).
CREATE UNIQUE INDEX IF NOT EXISTS uniq_messages_active_branch_per_group
  ON messages (parent_user_message_id)
  WHERE is_active_branch = true AND parent_user_message_id IS NOT NULL;

-- Loader index: pulls "user rows + active branches" cheaply.
CREATE INDEX IF NOT EXISTS idx_messages_convo_active
  ON messages (conversation_id, created_at)
  WHERE is_active_branch = true;

-- ── 3. Org-level sharing toggle ───────────────────────────────────────────
-- Admin kill-switch. Defaults to TRUE so existing orgs don't lose the
-- feature on migration; admins who want it off go set the flag.
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS allow_output_sharing BOOLEAN NOT NULL DEFAULT true;

-- ── 4. Shared outputs ─────────────────────────────────────────────────────
-- One row per share. We don't dedupe at the DB level by (message_id, active);
-- instead the POST handler short-circuits if an active token already exists,
-- so a revoked share + a fresh share produce two rows (one inactive, one
-- active) — preserving the audit trail of "what was once public".
CREATE TABLE IF NOT EXISTS shared_outputs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  created_by      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- 43-char base64url string from secrets.token_urlsafe(32). Indexed unique;
  -- collision odds are astronomical but the constraint is free defence.
  token           TEXT NOT NULL UNIQUE
    CHECK (char_length(token) BETWEEN 32 AND 80),
  is_active       BOOLEAN NOT NULL DEFAULT true,
  view_count      INTEGER NOT NULL DEFAULT 0,
  last_viewed_at  TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at      TIMESTAMPTZ
);

-- Public read path uses token → row lookup. UNIQUE constraint already
-- gives us a btree, so no second index needed.
CREATE INDEX IF NOT EXISTS idx_shared_outputs_message
  ON shared_outputs (message_id);
CREATE INDEX IF NOT EXISTS idx_shared_outputs_org_active
  ON shared_outputs (org_id, is_active);

ALTER TABLE shared_outputs ENABLE ROW LEVEL SECURITY;

-- Org-scoped SELECT for the share-management UI: "see who shared what".
DROP POLICY IF EXISTS shared_outputs_select ON shared_outputs;
CREATE POLICY shared_outputs_select ON shared_outputs
  FOR SELECT
  USING (org_id = auth_org_id());

-- Only the creator may mint a share, and only in their own org.
DROP POLICY IF EXISTS shared_outputs_insert ON shared_outputs;
CREATE POLICY shared_outputs_insert ON shared_outputs
  FOR INSERT
  WITH CHECK (
    org_id = auth_org_id()
    AND created_by = auth.uid()
  );

-- Creator or admin may revoke. The public anon GET runs under the
-- service role, so it bypasses these policies on purpose.
DROP POLICY IF EXISTS shared_outputs_update ON shared_outputs;
CREATE POLICY shared_outputs_update ON shared_outputs
  FOR UPDATE
  USING (
    org_id = auth_org_id()
    AND (
      created_by = auth.uid()
      OR (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
    )
  )
  WITH CHECK (org_id = auth_org_id());

DROP POLICY IF EXISTS shared_outputs_delete ON shared_outputs;
CREATE POLICY shared_outputs_delete ON shared_outputs
  FOR DELETE
  USING (
    org_id = auth_org_id()
    AND (
      created_by = auth.uid()
      OR (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
    )
  );

-- ── 5. Extend search_conversations to surface is_pinned ────────────────────
-- Sidebar search results need to render the pin icon, so the RPC's return
-- type grows by one column. Existing callers tolerate the extra column —
-- PostgREST's RPC binding is positional by name.
DROP FUNCTION IF EXISTS search_conversations(text, integer) CASCADE;

CREATE OR REPLACE FUNCTION search_conversations(
  search_query TEXT,
  result_limit INT DEFAULT 50
)
RETURNS TABLE (
  id UUID,
  title TEXT,
  is_pinned BOOLEAN,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  rank REAL
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  ts_q TSQUERY;
BEGIN
  IF search_query IS NULL OR length(trim(search_query)) = 0 THEN
    RETURN QUERY
    SELECT c.id, c.title, c.is_pinned, c.created_at, c.updated_at, 0::real AS rank
    FROM conversations c
    ORDER BY c.is_pinned DESC, c.updated_at DESC
    LIMIT result_limit;
    RETURN;
  END IF;

  ts_q := websearch_to_tsquery('english', search_query);

  RETURN QUERY
  WITH title_hits AS (
    SELECT
      c.id, c.title, c.is_pinned, c.created_at, c.updated_at,
      ts_rank(to_tsvector('english', coalesce(c.title, '')), ts_q) AS title_rank
    FROM conversations c
    WHERE to_tsvector('english', coalesce(c.title, '')) @@ ts_q
       OR c.title ILIKE '%' || search_query || '%'
  ),
  message_hits AS (
    SELECT m.conversation_id, MAX(ts_rank(m.content_tsv, ts_q)) AS msg_rank
    FROM messages m
    WHERE m.content_tsv @@ ts_q
    GROUP BY m.conversation_id
  ),
  fused AS (
    SELECT
      c.id, c.title, c.is_pinned, c.created_at, c.updated_at,
      coalesce(t.title_rank, 0) * 2.0 + coalesce(mh.msg_rank, 0) AS rank
    FROM conversations c
    LEFT JOIN title_hits   t  ON t.id = c.id
    LEFT JOIN message_hits mh ON mh.conversation_id = c.id
    WHERE t.id IS NOT NULL OR mh.conversation_id IS NOT NULL
  )
  SELECT f.id, f.title, f.is_pinned, f.created_at, f.updated_at, f.rank::real
  FROM fused f
  ORDER BY f.rank DESC, f.updated_at DESC
  LIMIT result_limit;
END;
$$;

GRANT EXECUTE ON FUNCTION search_conversations(TEXT, INT) TO authenticated, anon;
