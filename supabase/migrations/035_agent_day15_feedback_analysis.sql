-- ── Agent Roadmap Day 15 — Negative-feedback failure analysis
--
-- When a user thumbs-down an assistant message, an Inngest function (see
-- apps/api/app/inngest/feedback_functions.py) classifies the failure mode
-- via the LLM and persists the verdict back to the row. A weekly threshold
-- (>= 5 of the same failure_reason inside one rolling week) pages an admin.
--
-- We deliberately skip the `feedback_aggregations` materialized table the
-- roadmap suggests: the threshold query is a single indexed SELECT on
-- messages, the admin-alert dispatch already idempotency-checks via a
-- weekly dedupe key in email_events, and an agg table would add a second
-- source of truth that has to stay in sync with the per-message data.
-- We can add the agg table later if the admin dashboard ever needs it.
--
-- feedback_analysis JSONB shape:
--   {
--     "failure_reason": "wrong_tone" | "missing_context" | "outdated_policy"
--                     | "hallucination" | "wrong_format" | "unknown",
--     "suggested_doc": "Free-text suggestion of a document that would have helped",
--     "analysis_at":   ISO-8601 timestamp,
--     "model":         "gemini-2.0-flash" or whichever provider classified it,
--     "alerted_at":    Set when this row tipped a weekly threshold and an
--                      admin was paged. Lets us dedupe across the org per week.
--   }

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS feedback_analysis JSONB;

-- Partial index drives the weekly-threshold rollup. Postgres can use this
-- when filtering by org + the JSONB scalar; we ALSO include created_at so
-- the planner can range-scan the most recent week without a heap fetch.
CREATE INDEX IF NOT EXISTS idx_messages_feedback_failure_reason
  ON messages(org_id, (feedback_analysis->>'failure_reason'), created_at DESC)
  WHERE feedback = 'negative' AND feedback_analysis IS NOT NULL;

-- Lookup for "show me unanalyzed negative feedback" (admin debug + manual
-- re-trigger). Cheap, partial — only the rows that need attention.
CREATE INDEX IF NOT EXISTS idx_messages_feedback_pending_analysis
  ON messages(org_id, created_at DESC)
  WHERE feedback = 'negative' AND feedback_analysis IS NULL;

COMMENT ON COLUMN messages.feedback_analysis IS
  'Day 15: LLM-classified failure reason for negative feedback. NULL until the analyzer runs.';
