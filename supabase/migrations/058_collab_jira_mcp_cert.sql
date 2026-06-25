-- ── Migration 058 — Production Roadmap 2.7-2.10
--
-- Bundles four feature schemas into one migration so the production rollout
-- is atomic (one supabase db push, one rollback target):
--
--   1. Team Channels + Multiplayer Chat (2.7 — Real-Time Collaborative Chat)
--      conversation_participants table + conversations columns. Powers both
--      the "shared conversation" container AND multiplayer presence.
--
--   2. Jira Cloud as a task tracker (2.8 — Jira/Linear Integration)
--      Widen integrations.provider CHECK + action_items.external_provider
--      CHECK to admit 'jira'. The Linear half already shipped at 053.
--
--   3. MCP server (2.9 — Model Context Protocol)
--      mcp_tool_invocations audit table so admins can see what tools were
--      called via Claude/ChatGPT. Authn rides on existing api_keys.
--
--   4. Knowledge Certification (2.10)
--      knowledge_quizzes + certification_attempts tables. LLM-generated
--      multiple-choice quizzes attached to documents/policies; attempts
--      graded deterministically against the stored correct_index.
--
-- Each section is independently rollback-able by dropping the new objects;
-- the constraint widenings preserve all existing rows (additive only).

-- ── 1. Team Channels (2.7 prereq) ──────────────────────────────────────────
--
-- Design choices:
--
-- * `conversation_participants` is a join table — we do NOT add a participants
--   ARRAY column to `conversations`. RLS policies and "fetch my channels"
--   queries are dramatically simpler with a normalized table, and we need to
--   carry per-participant state (joined_at, last_read_at) anyway.
--
-- * `conversations.is_channel` is the discriminator between a private 1:1
--   chat (legacy single-user_id behaviour, no participant rows) and a
--   multi-user channel. Legacy conversations stay is_channel=false and
--   never grow participant rows — no backfill needed.
--
-- * `channel_visibility` is set on the conversations row itself, not the
--   participants. 'private' = only listed participants can read. 'org' =
--   any org member can read (Slack "public channel" semantics).
--
-- * RLS allows reading a conversation when:
--   - The legacy single-user case (user_id = auth.uid()) — unchanged.
--   - The channel case (member of participants OR visibility='org').

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS is_channel BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS channel_visibility TEXT
    CHECK (channel_visibility IN ('private', 'org'))
    DEFAULT 'private',
  ADD COLUMN IF NOT EXISTS topic TEXT;

COMMENT ON COLUMN conversations.is_channel IS
  'When TRUE this row is a multi-user channel (see conversation_participants). When FALSE this is a legacy private chat owned by user_id alone.';
COMMENT ON COLUMN conversations.channel_visibility IS
  'private = listed participants only. org = any org member can join and read.';


CREATE TABLE IF NOT EXISTS conversation_participants (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role            TEXT NOT NULL DEFAULT 'editor'
    CHECK (role IN ('owner', 'editor', 'viewer')),
  joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_read_at    TIMESTAMPTZ,
  -- Avatar colour seed — derived client-side from user_id, stored here so
  -- the channel header is stable across renders without re-derivation.
  color_seed      TEXT,
  UNIQUE (conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_participants_user
  ON conversation_participants(user_id, org_id);
CREATE INDEX IF NOT EXISTS idx_conv_participants_conv
  ON conversation_participants(conversation_id);

ALTER TABLE conversation_participants ENABLE ROW LEVEL SECURITY;

-- Members can see the roster of any channel they belong to OR any 'org'
-- visibility channel in their org. Admins always see all.
DROP POLICY IF EXISTS cp_select ON conversation_participants;
CREATE POLICY cp_select ON conversation_participants
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      user_id = auth.uid()
      OR EXISTS (
        SELECT 1 FROM conversation_participants p2
        WHERE p2.conversation_id = conversation_participants.conversation_id
          AND p2.user_id = auth.uid()
      )
      OR EXISTS (
        SELECT 1 FROM conversations c
        WHERE c.id = conversation_participants.conversation_id
          AND c.channel_visibility = 'org'
      )
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );

-- Writes (invite / leave) go through the service role — the router validates
-- the caller is an owner of the channel before mutating. Keeps the policy
-- simple and avoids cross-table joins on every write.

-- Widen the conversations SELECT RLS to admit channel members. We replicate
-- the legacy policy first (in case 001 named it differently across stamps)
-- then install the new shape.
DROP POLICY IF EXISTS conversations_select_org ON conversations;
DROP POLICY IF EXISTS conversations_select_self ON conversations;
DROP POLICY IF EXISTS conversations_select ON conversations;

CREATE POLICY conversations_select ON conversations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      -- Legacy single-user conversation
      user_id = auth.uid()
      -- Multi-user channel: caller is a participant
      OR EXISTS (
        SELECT 1 FROM conversation_participants cp
        WHERE cp.conversation_id = conversations.id
          AND cp.user_id = auth.uid()
      )
      -- Org-visibility channel: any org member can read
      OR (is_channel = true AND channel_visibility = 'org')
      -- Admins see everything (unchanged from legacy)
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );

-- Messages RLS: the existing policy gated on `conversations.user_id = auth.uid()`.
-- We can't easily inspect existing names, so we DROP IF EXISTS the common
-- variants and reinstall a shape that admits channel members. Messages are
-- read-only via this policy; writes still go through the service role from
-- the chat router.
DROP POLICY IF EXISTS messages_select_org ON messages;
DROP POLICY IF EXISTS messages_select_owner ON messages;
DROP POLICY IF EXISTS messages_select ON messages;

CREATE POLICY messages_select ON messages
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM conversations c
      WHERE c.id = messages.conversation_id
        AND (
          c.user_id = auth.uid()
          OR EXISTS (
            SELECT 1 FROM conversation_participants cp
            WHERE cp.conversation_id = c.id AND cp.user_id = auth.uid()
          )
          OR (c.is_channel = true AND c.channel_visibility = 'org')
          OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
        )
    )
  );


-- ── 2. Jira admission (2.8) ────────────────────────────────────────────────
-- Widen both CHECK constraints. 'jira' rides the unified integrations table
-- (per-user OAuth) and slots into the same action_items.external_provider
-- column Linear uses.

ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_provider_check;
ALTER TABLE integrations
  ADD CONSTRAINT integrations_provider_check CHECK (
    provider IN (
      'onedrive', 'confluence', 'github', 'dropbox',
      'greenhouse', 'lever', 'ashby',
      'google_workspace',
      'asana', 'linear', 'jira'
    )
  );

ALTER TABLE action_items DROP CONSTRAINT IF EXISTS action_items_external_provider_check;
-- The original constraint was an inline CHECK on the column; PG renamed it
-- with the implicit `<table>_<column>_check` name. Drop generously then add
-- the canonical one.
DO $$
BEGIN
  ALTER TABLE action_items DROP CONSTRAINT action_items_external_provider_check1;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;
DO $$
BEGIN
  ALTER TABLE action_items DROP CONSTRAINT IF EXISTS action_items_external_provider_check2;
EXCEPTION WHEN undefined_object THEN NULL;
END $$;

-- Strip every CHECK constraint on action_items.external_provider so we can
-- reinstall it cleanly. There's no portable "drop check by column" in PG, so
-- we look it up by checking pg_constraint.
DO $$
DECLARE
  cname TEXT;
BEGIN
  FOR cname IN
    SELECT conname FROM pg_constraint
     WHERE conrelid = 'action_items'::regclass
       AND contype = 'c'
       AND pg_get_constraintdef(oid) ILIKE '%external_provider%'
  LOOP
    EXECUTE format('ALTER TABLE action_items DROP CONSTRAINT %I', cname);
  END LOOP;
END $$;

ALTER TABLE action_items
  ADD CONSTRAINT action_items_external_provider_check
    CHECK (external_provider IN ('notion', 'asana', 'linear', 'jira'));


-- ── 3. MCP audit (2.9) ─────────────────────────────────────────────────────
-- One row per MCP tool invocation. Lets admins audit "what is Claude doing
-- in our workspace" without parsing logs. Append-only; pruned by the
-- query_log_retention cron (extend the retention rule in a follow-up).

CREATE TABLE IF NOT EXISTS mcp_tool_invocations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  api_key_id      UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  tool_name       TEXT NOT NULL,
  arguments       JSONB NOT NULL DEFAULT '{}',
  -- Truncated result preview — we never persist full document text
  -- (regulated content lives in `chunks` and is fetched fresh on each call).
  result_summary  TEXT,
  status          TEXT NOT NULL DEFAULT 'ok'
    CHECK (status IN ('ok', 'error')),
  error           TEXT,
  latency_ms      INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mcp_inv_org_created
  ON mcp_tool_invocations(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mcp_inv_key_created
  ON mcp_tool_invocations(api_key_id, created_at DESC);

ALTER TABLE mcp_tool_invocations ENABLE ROW LEVEL SECURITY;

-- Admins read; everyone else opaque. Writes are service-role only.
DROP POLICY IF EXISTS mcp_inv_select_admin ON mcp_tool_invocations;
CREATE POLICY mcp_inv_select_admin ON mcp_tool_invocations
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
  );


-- ── 4. Knowledge Certification (2.10) ──────────────────────────────────────
--
-- Two tables: quizzes (the question bank, one per document version) and
-- attempts (one row per user submission). Quizzes are LLM-generated once per
-- (document, version) and cached — regeneration creates a new quiz row with
-- is_active=true and flips the prior one to is_active=false (preserves an
-- audit trail of "what test was run").
--
-- Grading is purely a server-side index comparison; no LLM-in-the-loop, no
-- AI judgement. Auditors get a deterministic answer to "was the test fair?"

CREATE TABLE IF NOT EXISTS knowledge_quizzes (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  document_id           UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  document_version_id   UUID REFERENCES document_versions(id) ON DELETE CASCADE,
  -- Array of {id, prompt, choices: [str x 4], correct_index, source_chunk_ids: [uuid]}
  -- Stored as JSONB so the question schema can evolve without migrations.
  -- Validated by app/services/certifications.py at write time.
  questions             JSONB NOT NULL,
  passing_score         NUMERIC NOT NULL DEFAULT 0.80
    CHECK (passing_score >= 0 AND passing_score <= 1),
  question_count        INTEGER NOT NULL DEFAULT 5,
  created_by            UUID NOT NULL REFERENCES users(id),
  is_active             BOOLEAN NOT NULL DEFAULT TRUE,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one active quiz per (document, version). Repeated generation flips
-- the prior is_active=false in a transaction (see services/certifications.py).
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_quiz_per_doc_version
  ON knowledge_quizzes (document_id, COALESCE(document_version_id, '00000000-0000-0000-0000-000000000000'::uuid))
  WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS idx_quizzes_org_active
  ON knowledge_quizzes(org_id, is_active)
  WHERE is_active = TRUE;

ALTER TABLE knowledge_quizzes ENABLE ROW LEVEL SECURITY;

-- Members read the active quiz to take it. Admins read all (active + archived).
DROP POLICY IF EXISTS quizzes_select ON knowledge_quizzes;
CREATE POLICY quizzes_select ON knowledge_quizzes
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      is_active = TRUE
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );


CREATE TABLE IF NOT EXISTS certification_attempts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  quiz_id         UUID NOT NULL REFERENCES knowledge_quizzes(id) ON DELETE CASCADE,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  -- Array of {question_id, selected_index}
  answers         JSONB NOT NULL DEFAULT '[]',
  score           NUMERIC NOT NULL DEFAULT 0
    CHECK (score >= 0 AND score <= 1),
  correct_count   INTEGER NOT NULL DEFAULT 0,
  total_count     INTEGER NOT NULL DEFAULT 0,
  passed          BOOLEAN NOT NULL DEFAULT FALSE,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at    TIMESTAMPTZ,
  -- Optional link to the matching acknowledgement row (set on successful
  -- pass — flips the ack to 'acknowledged' atomically).
  acknowledgement_id UUID REFERENCES acknowledgements(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_user_quiz
  ON certification_attempts(user_id, quiz_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_org_completed
  ON certification_attempts(org_id, completed_at DESC)
  WHERE passed = TRUE;
CREATE INDEX IF NOT EXISTS idx_attempts_doc
  ON certification_attempts(document_id, passed);

ALTER TABLE certification_attempts ENABLE ROW LEVEL SECURITY;

-- User sees own attempts. Admins see all.
DROP POLICY IF EXISTS attempts_select_self_or_admin ON certification_attempts;
CREATE POLICY attempts_select_self_or_admin ON certification_attempts
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      user_id = auth.uid()
      OR EXISTS (SELECT 1 FROM users WHERE id = auth.uid() AND role = 'admin')
    )
  );


-- Tell documents that the cert system can be required separately from a
-- plain "click to ack". Defaults FALSE so existing policy docs keep working
-- as before; admins flip it per-document when they want comprehension gating.
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS requires_certification BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN documents.requires_certification IS
  '2.10: when TRUE, users must pass the active knowledge_quiz for this doc (>= passing_score) before their acknowledgement is accepted. Implies requires_acknowledgement.';


-- ── Comments for future maintainers ────────────────────────────────────────

COMMENT ON TABLE conversation_participants IS
  '2.7: roster of users that can read/write a multi-user channel. Legacy private conversations have no rows here.';
COMMENT ON TABLE mcp_tool_invocations IS
  '2.9: audit log of MCP tool calls (Claude/ChatGPT calling NirnayaIQ as a tool). Append-only.';
COMMENT ON TABLE knowledge_quizzes IS
  '2.10: LLM-generated multiple-choice quizzes per (document, version). One active row per doc; regeneration flips is_active.';
COMMENT ON TABLE certification_attempts IS
  '2.10: one row per user quiz submission. Graded deterministically against questions[i].correct_index.';
