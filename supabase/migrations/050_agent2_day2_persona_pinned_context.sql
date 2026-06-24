-- ── Agent Roadmap 2 — Day 2: Persona Modes (#37) + Context Pinning (#39)
--
-- Two small, additive columns:
--
-- 1. users.persona — distinct from users.role (which is the access tier:
--    admin | member). Persona is a function-style label (hr, sales, …) that
--    influences how chat answers are framed for that user. Storing as a
--    separate column avoids polluting the existing role CHECK constraint
--    and keeps access control orthogonal from style.
--
-- 2. conversations.pinned_context — a freeform per-conversation context
--    string that gets injected into the system prompt for every turn in
--    that conversation. Different from scoped_document_id / scoped_tags
--    (which control *which* docs are searched). Pinned context controls
--    *what the user wants the LLM to keep in mind* — e.g. "we're writing
--    for the Q3 enterprise renewal cohort", "all responses should reference
--    the launch on June 30".
--
-- Length cap on pinned_context is 2KB — large enough for a paragraph of
-- context, small enough that one rogue conversation can't blow up the
-- system prompt budget on every turn for the life of the conversation.

-- ── Persona ────────────────────────────────────────────────────────────

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS persona TEXT;

ALTER TABLE users
  DROP CONSTRAINT IF EXISTS users_persona_check;
ALTER TABLE users
  ADD CONSTRAINT users_persona_check
  CHECK (
    persona IS NULL
    OR persona IN ('hr', 'sales', 'engineering', 'finance', 'operations', 'executive')
  );

COMMENT ON COLUMN users.persona IS
  'Agent2 Day 2 #37: function-style overlay (hr/sales/engineering/finance/operations/executive). Distinct from users.role (access tier). Drives system-prompt overlay in task_chain.py.';


-- ── Pinned context ─────────────────────────────────────────────────────

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS pinned_context TEXT;

ALTER TABLE conversations
  DROP CONSTRAINT IF EXISTS conversations_pinned_context_len;
ALTER TABLE conversations
  ADD CONSTRAINT conversations_pinned_context_len
  CHECK (pinned_context IS NULL OR char_length(pinned_context) <= 2000);

COMMENT ON COLUMN conversations.pinned_context IS
  'Agent2 Day 2 #39: freeform user-pinned context injected into every turn''s system prompt. Capped at 2000 chars.';
