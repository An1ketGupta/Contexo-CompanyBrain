-- Persist the Langfuse trace id alongside each assistant message so we can
-- forward thumbs-up/down feedback as scores attached to the exact trace that
-- produced the answer. Without this, "user_feedback" scores would have to be
-- correlated by timestamp + message content — too brittle.
--
-- Nullable on purpose: messages created before Langfuse was enabled (or while
-- the env keys are empty in dev) won't have a trace id, and that's fine —
-- the feedback handler simply skips the score call.

ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS langfuse_trace_id TEXT;

-- We only ever look up trace_id by message_id (FK is the PK on messages), so
-- no extra index is needed beyond the PK. If we later add reverse lookups
-- (trace_id → message_id from a Langfuse webhook), revisit.
