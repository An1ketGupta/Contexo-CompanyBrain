-- ── Conversation archive (V3 #104, production-grade) ──────────────────────
--
-- Adds soft-archive + per-org retention to `conversations`. Three things
-- this design improves over the roadmap spec:
--
--  1. `last_accessed_at` is tracked separately from `updated_at`.
--     A conversation being *read* should keep it out of the archive cron;
--     `updated_at` only changes on writes (new messages, rename, pin).
--     The chat router throttles writes to last_accessed_at to one update
--     per hour per conversation, so this stays cheap.
--
--  2. `archived_by` + `archive_reason` for audit + UI labelling.
--     `archived_by IS NULL` means the daily cron archived it; otherwise it
--     records the human who clicked "Archive". `archive_reason` carries
--     the same intent in text form for analytics + observability.
--
--  3. Three *partial* indexes instead of one wide composite:
--      • Hot path  (sidebar load)          — only active rows
--      • Cron scan (auto-archive candidate)— only active + unpinned
--      • Archive page                      — only archived rows
--     Each is a fraction of the table, so the sidebar query stays O(log N
--     of active rows) regardless of how big the archive grows.
--
-- The cron + delete pipeline lives in
--   apps/api/app/inngest/archive_functions.py.

-- ── 1. New columns ────────────────────────────────────────────────────────
ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_archived       BOOLEAN     NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS archived_at       TIMESTAMPTZ,
  -- NULL = archived by the auto-archive cron (system action). A populated
  -- value points at the user who hit "Archive" from the UI.
  ADD COLUMN IF NOT EXISTS archived_by       UUID        REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS archive_reason    TEXT
    CHECK (archive_reason IS NULL
           OR archive_reason IN ('auto_inactive', 'manual', 'bulk_manual')),
  -- Default the legacy rows to their last write time so the first cron run
  -- doesn't suddenly flag a 100-day-old conversation that was being viewed
  -- last week. updated_at is the closest proxy we have for "was touched".
  ADD COLUMN IF NOT EXISTS last_accessed_at  TIMESTAMPTZ NOT NULL DEFAULT now();

-- Backfill last_accessed_at from updated_at for any rows that pre-date this
-- migration. The DEFAULT now() above covers fresh INSERTs but leaves legacy
-- rows at "right now", which would defeat the cron for a month. Fix that.
UPDATE conversations
   SET last_accessed_at = updated_at
 WHERE last_accessed_at >= updated_at
   AND created_at < now() - interval '1 minute';

-- ── 2. Partial indexes (the perf story) ───────────────────────────────────
-- (a) Sidebar load — pinned-first, then recency. Filtered to active.
--     Planner picks this when `WHERE is_archived = false` is in the query.
CREATE INDEX IF NOT EXISTS idx_conversations_active
  ON conversations (user_id, is_pinned DESC, updated_at DESC)
  WHERE is_archived = false;

-- (b) Cron scan — "archive candidates: active, unpinned, last touched > X".
--     Tiny index even at scale; the unpinned + active filter usually keeps
--     it under a few percent of the table.
CREATE INDEX IF NOT EXISTS idx_conversations_archive_candidate
  ON conversations (org_id, last_accessed_at)
  WHERE is_archived = false AND is_pinned = false;

-- (c) Archive page — sorted by archived_at, scoped to the user.
CREATE INDEX IF NOT EXISTS idx_conversations_archived
  ON conversations (user_id, archived_at DESC)
  WHERE is_archived = true;

-- ── 3. Pinning protects against archive — make it cheap to verify ─────────
-- Trigger guard: never allow setting `is_archived = true` on a pinned row.
-- The cron filters this out at the SQL level, but a stray PATCH from the
-- API would bypass that. A trigger is the right place to enforce an
-- invariant that spans both code paths.
CREATE OR REPLACE FUNCTION conversations_prevent_archive_if_pinned()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.is_archived = true AND NEW.is_pinned = true THEN
    RAISE EXCEPTION 'Cannot archive a pinned conversation. Unpin first.'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_conversations_no_archive_when_pinned ON conversations;
CREATE TRIGGER trg_conversations_no_archive_when_pinned
  BEFORE INSERT OR UPDATE OF is_archived, is_pinned ON conversations
  FOR EACH ROW
  EXECUTE FUNCTION conversations_prevent_archive_if_pinned();

-- ── 4. RPC: bulk-archive eligible conversations across all orgs ───────────
-- Single statement, no N+1. The CTE pulls each org's threshold out of
-- metadata.archive (with sensible defaults), joins to conversations, and
-- archives anything past the cutoff that's still active and unpinned. The
-- function returns aggregated stats so the Inngest cron can log them.
--
-- Defaults: 45 days, auto-archive enabled. Both knobs live in org metadata
-- so an admin can edit them without a schema change.
CREATE OR REPLACE FUNCTION run_conversation_auto_archive()
RETURNS TABLE (org_id UUID, archived_count INT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN QUERY
  WITH org_thresholds AS (
    SELECT
      o.id                                          AS org_id,
      COALESCE(
        (o.metadata #>> '{archive,threshold_days}')::int,
        45
      )                                             AS threshold_days,
      COALESCE(
        (o.metadata #>> '{archive,auto_archive_enabled}')::boolean,
        true
      )                                             AS enabled
    FROM organizations o
  ),
  archive_targets AS (
    SELECT c.id, c.org_id
    FROM conversations c
    JOIN org_thresholds t ON t.org_id = c.org_id
    WHERE t.enabled = true
      AND c.is_archived = false
      AND c.is_pinned   = false
      AND c.last_accessed_at < now() - make_interval(days => t.threshold_days)
  ),
  archived AS (
    UPDATE conversations c
       SET is_archived    = true,
           archived_at    = now(),
           archived_by    = NULL,
           archive_reason = 'auto_inactive'
      FROM archive_targets at
     WHERE c.id = at.id
    RETURNING c.org_id
  )
  SELECT a.org_id, count(*)::int AS archived_count
  FROM archived a
  GROUP BY a.org_id;
END;
$$;

REVOKE ALL ON FUNCTION run_conversation_auto_archive() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION run_conversation_auto_archive() TO service_role;

-- ── 5. RPC: bulk hard-delete past-threshold archived conversations ────────
-- Honours the optional per-org `delete_after_archive_days` setting. NULL or
-- missing = never delete (the default). Returns aggregated stats for the
-- cron logger. CASCADE on messages handles the dependent rows.
CREATE OR REPLACE FUNCTION run_conversation_auto_delete()
RETURNS TABLE (org_id UUID, deleted_count INT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  RETURN QUERY
  WITH org_thresholds AS (
    SELECT
      o.id                                                   AS org_id,
      (o.metadata #>> '{archive,delete_after_archive_days}')::int
                                                             AS delete_after_days
    FROM organizations o
  ),
  delete_targets AS (
    SELECT c.id, c.org_id
    FROM conversations c
    JOIN org_thresholds t ON t.org_id = c.org_id
    WHERE t.delete_after_days IS NOT NULL
      AND t.delete_after_days > 0
      AND c.is_archived = true
      AND c.archived_at < now() - make_interval(days => t.delete_after_days)
  ),
  deleted AS (
    DELETE FROM conversations c
     USING delete_targets dt
     WHERE c.id = dt.id
    RETURNING c.org_id
  )
  SELECT d.org_id, count(*)::int AS deleted_count
  FROM deleted d
  GROUP BY d.org_id;
END;
$$;

REVOKE ALL ON FUNCTION run_conversation_auto_delete() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION run_conversation_auto_delete() TO service_role;

-- ── 6. Extend search_conversations to surface archive state ───────────────
-- The sidebar search needs to know if a hit is archived so it can render a
-- subtle badge. Same signature growth pattern we used in migration 019.
DROP FUNCTION IF EXISTS search_conversations(text, integer) CASCADE;

CREATE OR REPLACE FUNCTION search_conversations(
  search_query TEXT,
  result_limit INT DEFAULT 50
)
RETURNS TABLE (
  id           UUID,
  title        TEXT,
  is_pinned    BOOLEAN,
  is_archived  BOOLEAN,
  archived_at  TIMESTAMPTZ,
  created_at   TIMESTAMPTZ,
  updated_at   TIMESTAMPTZ,
  rank         REAL
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
    SELECT c.id, c.title, c.is_pinned, c.is_archived, c.archived_at,
           c.created_at, c.updated_at, 0::real AS rank
    FROM conversations c
    WHERE c.is_archived = false  -- empty-query path = sidebar list; never archived
    ORDER BY c.is_pinned DESC, c.updated_at DESC
    LIMIT result_limit;
    RETURN;
  END IF;

  ts_q := websearch_to_tsquery('english', search_query);

  RETURN QUERY
  WITH title_hits AS (
    SELECT
      c.id, c.title, c.is_pinned, c.is_archived, c.archived_at,
      c.created_at, c.updated_at,
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
      c.id, c.title, c.is_pinned, c.is_archived, c.archived_at,
      c.created_at, c.updated_at,
      coalesce(t.title_rank, 0) * 2.0 + coalesce(mh.msg_rank, 0) AS rank
    FROM conversations c
    LEFT JOIN title_hits   t  ON t.id = c.id
    LEFT JOIN message_hits mh ON mh.conversation_id = c.id
    WHERE t.id IS NOT NULL OR mh.conversation_id IS NOT NULL
  )
  -- Search results DO include archived hits so they're discoverable.
  -- The frontend renders an "Archived" badge based on is_archived.
  SELECT f.id, f.title, f.is_pinned, f.is_archived, f.archived_at,
         f.created_at, f.updated_at, f.rank::real
  FROM fused f
  ORDER BY f.rank DESC, f.updated_at DESC
  LIMIT result_limit;
END;
$$;

GRANT EXECUTE ON FUNCTION search_conversations(TEXT, INT) TO authenticated, anon;
