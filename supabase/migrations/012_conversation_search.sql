-- ── Conversation search (Day 9 / #21) ───────────────────────────────────────
-- Sidebar search needs to match both:
--   1. Conversation titles (rare, short, often the only hit)
--   2. Message contents (where the real signal lives — users remember what
--      they asked, not what they named the chat)
--
-- We add a generated tsvector on `messages.content` mirroring the existing
-- pattern on `chunks.content_tsv` (migration 001). GIN indexes back both
-- the message tsvector and the conversation title.
--
-- The actual query joins messages → conversations and ORs the matches
-- together so a hit on either column surfaces the conversation row.

-- Messages: full-text content vector
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS content_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

CREATE INDEX IF NOT EXISTS idx_messages_content_tsv
  ON messages USING GIN (content_tsv);

-- Conversation title: expression GIN index (no generated column needed for
-- a single small text field; the expression index is enough).
CREATE INDEX IF NOT EXISTS idx_conversations_title_tsv
  ON conversations USING GIN (to_tsvector('english', coalesce(title, '')));


-- ── Helper RPC: search_conversations ────────────────────────────────────────
-- Wraps the title-OR-message join into a single round-trip and pushes the
-- "did any message in this conversation match?" check into PostgreSQL where
-- it belongs. The function is SECURITY INVOKER and runs through RLS — it
-- only ever returns rows the caller's JWT can see.
--
-- Ordering:
--   * No query → updated_at DESC (the default sidebar order)
--   * With query → relevance score (title hits weighted higher than message
--                  hits), then updated_at DESC as the tie-breaker

CREATE OR REPLACE FUNCTION search_conversations(
  search_query TEXT,
  result_limit INT DEFAULT 50
)
RETURNS TABLE (
  id UUID,
  title TEXT,
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
    SELECT c.id, c.title, c.created_at, c.updated_at, 0::real AS rank
    FROM conversations c
    ORDER BY c.updated_at DESC
    LIMIT result_limit;
    RETURN;
  END IF;

  -- websearch_to_tsquery handles user-shaped input gracefully (quoted phrases,
  -- OR keyword, leading minus for negation) and never throws on bad syntax.
  ts_q := websearch_to_tsquery('english', search_query);

  RETURN QUERY
  WITH title_hits AS (
    SELECT
      c.id,
      c.title,
      c.created_at,
      c.updated_at,
      ts_rank(to_tsvector('english', coalesce(c.title, '')), ts_q) AS title_rank
    FROM conversations c
    WHERE to_tsvector('english', coalesce(c.title, '')) @@ ts_q
       OR c.title ILIKE '%' || search_query || '%'
  ),
  message_hits AS (
    SELECT
      m.conversation_id,
      MAX(ts_rank(m.content_tsv, ts_q)) AS msg_rank
    FROM messages m
    WHERE m.content_tsv @@ ts_q
    GROUP BY m.conversation_id
  ),
  fused AS (
    SELECT
      c.id,
      c.title,
      c.created_at,
      c.updated_at,
      -- Title hits beat message hits 2:1; both feed into a single ordered list.
      coalesce(t.title_rank, 0) * 2.0 + coalesce(mh.msg_rank, 0) AS rank
    FROM conversations c
    LEFT JOIN title_hits t ON t.id = c.id
    LEFT JOIN message_hits mh ON mh.conversation_id = c.id
    WHERE t.id IS NOT NULL OR mh.conversation_id IS NOT NULL
  )
  SELECT f.id, f.title, f.created_at, f.updated_at, f.rank::real
  FROM fused f
  ORDER BY f.rank DESC, f.updated_at DESC
  LIMIT result_limit;
END;
$$;

GRANT EXECUTE ON FUNCTION search_conversations(TEXT, INT) TO authenticated, anon;
