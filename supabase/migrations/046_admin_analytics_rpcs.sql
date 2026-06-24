-- 046_admin_analytics_rpcs.sql
-- Day 9: push admin analytics aggregations into Postgres so the router never
-- pulls 50k raw rows into Python.  All functions use SECURITY INVOKER so they
-- run under the service-role key passed by FastAPI — no privilege escalation.

BEGIN;

-- ── 1. Daily query counts (replaces 50k messages pull + Python bucketing) ────
CREATE OR REPLACE FUNCTION get_admin_daily_query_counts(
    p_org_id   UUID,
    p_start    TIMESTAMPTZ,
    p_end      TIMESTAMPTZ
)
RETURNS TABLE(day DATE, count BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT
        (date_trunc('day', created_at AT TIME ZONE 'UTC'))::date AS day,
        count(*)                                                   AS count
    FROM messages
    WHERE org_id  = p_org_id
      AND role    = 'user'
      AND created_at >= p_start
      AND created_at <  p_end
    GROUP BY 1
    ORDER BY 1;
$$;

-- ── 2. Active user count (replaces 10k conversations pull + Python DISTINCT) ─
CREATE OR REPLACE FUNCTION get_admin_active_user_count(
    p_org_id UUID,
    p_start  TIMESTAMPTZ
)
RETURNS TABLE(count BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT count(DISTINCT user_id) AS count
    FROM conversations
    WHERE org_id    = p_org_id
      AND updated_at >= p_start;
$$;

-- ── 3. Per-user query stats (replaces 50k messages + chunked conv join) ───────
-- Returns at most 50 rows (top users by query volume) to cap email-lookup cost.
CREATE OR REPLACE FUNCTION get_admin_per_user_stats(
    p_org_id UUID,
    p_start  TIMESTAMPTZ
)
RETURNS TABLE(user_id UUID, queries BIGINT, last_active TIMESTAMPTZ)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT
        c.user_id,
        count(m.id)     AS queries,
        max(m.created_at) AS last_active
    FROM messages m
    JOIN conversations c ON m.conversation_id = c.id
    WHERE m.org_id      = p_org_id
      AND m.role        = 'user'
      AND m.created_at  >= p_start
      AND c.user_id IS NOT NULL
    GROUP BY c.user_id
    ORDER BY queries DESC
    LIMIT 50;
$$;

-- ── 4. Top cited documents with names (replaces 20k citation pull) ────────────
CREATE OR REPLACE FUNCTION get_admin_top_cited_docs(
    p_org_id UUID,
    p_start  TIMESTAMPTZ,
    p_limit  INT DEFAULT 5
)
RETURNS TABLE(document_id UUID, name TEXT, citation_count BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT
        cc.document_id,
        d.name,
        count(*)  AS citation_count
    FROM chunk_citations cc
    JOIN documents d ON cc.document_id = d.id
    WHERE cc.org_id   = p_org_id
      AND cc.cited_at >= p_start
      AND d.name IS NOT NULL
    GROUP BY cc.document_id, d.name
    ORDER BY citation_count DESC
    LIMIT p_limit;
$$;

-- ── 5. Distinct cited-doc count (replaces second 10k citation pull) ───────────
CREATE OR REPLACE FUNCTION get_admin_cited_doc_count(
    p_org_id UUID,
    p_start  TIMESTAMPTZ
)
RETURNS TABLE(count BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT count(DISTINCT document_id) AS count
    FROM chunk_citations
    WHERE org_id   = p_org_id
      AND cited_at >= p_start;
$$;

-- ── 6. Intent + engagement stats (replaces 50k assistant message pull) ────────
CREATE OR REPLACE FUNCTION get_admin_intent_stats(
    p_org_id UUID,
    p_start  TIMESTAMPTZ
)
RETURNS TABLE(
    intent        TEXT,
    cnt           BIGINT,
    copied_cnt    BIGINT,
    minutes_saved BIGINT
)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT
        (metadata->>'intent')                                                          AS intent,
        count(*)                                                                        AS cnt,
        count(*) FILTER (WHERE copy_count IS NOT NULL AND copy_count > 0)              AS copied_cnt,
        coalesce(sum(time_saved_minutes), 0)::BIGINT                                   AS minutes_saved
    FROM messages
    WHERE org_id     = p_org_id
      AND role       = 'assistant'
      AND created_at >= p_start
      AND (metadata->>'intent') IS NOT NULL
    GROUP BY 1;
$$;

-- ── 7. Feedback counts (replaces 10k analytics_events pull) ──────────────────
CREATE OR REPLACE FUNCTION get_admin_feedback_counts(
    p_org_id UUID,
    p_start  TIMESTAMPTZ
)
RETURNS TABLE(feedback TEXT, cnt BIGINT)
LANGUAGE sql STABLE SECURITY INVOKER
AS $$
    SELECT
        (metadata->>'feedback') AS feedback,
        count(*)                AS cnt
    FROM analytics_events
    WHERE org_id      = p_org_id
      AND event_type  = 'feedback_given'
      AND created_at  >= p_start
      AND (metadata->>'feedback') IN ('positive', 'negative')
    GROUP BY 1;
$$;

COMMIT;
