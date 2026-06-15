-- ── Agent Roadmap Day 12 — Support Response Agent + Auto-Tagger
--
-- Two unrelated-looking changes ride in the same migration because they
-- ship in the same agent-roadmap day and both are tiny:
--
--   1. support_tickets — every inbound email classified as support/sales
--      gets a ticket row. The AI draft lives in the existing messages table
--      (linked via ai_draft_message_id) so the Day 6 approval/dispatch
--      pipeline can flow it through Gmail with no parallel mechanism.
--
--   2. documents.auto_tagged — boolean flag the documents UI uses to show
--      an "AI" pill next to tag chips that came from the auto-tagger.
--      We deliberately do NOT track per-tag provenance: the tags array is
--      a single set and a user can append/remove freely; the boolean only
--      records "the agent contributed to this set at least once". If the
--      user later adds manual tags, auto_tagged stays true — that's fine
--      because the UI just uses it to decide whether to show the badge.
--
-- Schema choices for support_tickets:
--   * status is a TEXT (no enum) so a new lifecycle state can ship without
--     a migration. The router enforces the allowed transitions in code.
--   * category is also free-text — the classifier emits canonical labels
--     ('support' | 'sales' | 'internal' | 'knowledge') but we don't want a
--     CHECK constraint blocking a future label.
--   * classifier metadata (confidence + reason) is kept inline so we can
--     debug misclassifications without joining another table.
--   * agent_run_id references agent_runs so the admin audit page can
--     navigate ticket → agent run → step log without round-tripping.

CREATE TABLE IF NOT EXISTS support_tickets (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  -- Inbound envelope (subject capped at 200 to match email_forward.parse_envelope).
  from_email               TEXT NOT NULL,
  from_name                TEXT,
  subject                  TEXT NOT NULL,
  body                     TEXT NOT NULL,

  -- Classifier output.
  category                 TEXT NOT NULL DEFAULT 'support',
  classifier_confidence    REAL,                       -- 0..1
  classifier_reason        TEXT,                       -- short string for audit

  -- Drafting state. The draft body lives directly on the ticket row — the
  -- autonomous agent has no user context, and `conversations.user_id` is
  -- NOT NULL, so we can't safely create a message inline. When an admin
  -- promotes the draft to "Send via Gmail" we mint a message at that point
  -- (owned by the admin clicking Send) and link it via ai_draft_message_id
  -- so existing delivery_status / approval plumbing reuses unchanged.
  ai_draft_body            TEXT,
  ai_draft_sources         JSONB,                      -- [{document_id, document_name, page_number, excerpt}, …]
  ai_draft_confidence      REAL,                       -- 0..10 (matches messages.metadata.confidence.score)
  ai_draft_message_id      UUID REFERENCES messages(id) ON DELETE SET NULL,
  agent_run_id             UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  -- Lifecycle: pending → drafted → sent | rejected.
  --            pending → drafting_failed (drafting LLM error)
  --            pending → no_draft (low-confidence skip; admin handles manually)
  status                   TEXT NOT NULL DEFAULT 'pending',

  -- Optional dedupe of the inbound (sha256(subject + first 256b body)).
  -- Same shape as email_forward.upsert_external_document uses. Two forwards
  -- of the same email shouldn't open two tickets.
  inbound_sig              TEXT,

  -- Outcome (what the human did).
  resolved_by              UUID REFERENCES users(id) ON DELETE SET NULL,
  resolved_at              TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_org_created
  ON support_tickets(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_tickets_org_status_created
  ON support_tickets(org_id, status, created_at DESC);

-- Dedupe lookup is per-org; partial index excludes the NULLs (legacy rows).
CREATE UNIQUE INDEX IF NOT EXISTS uq_support_tickets_org_inbound_sig
  ON support_tickets(org_id, inbound_sig)
  WHERE inbound_sig IS NOT NULL;

-- updated_at trigger — inlined touch function matches the per-table style
-- used elsewhere in this codebase (see 018_v3_day1_2 prompt_templates).
CREATE OR REPLACE FUNCTION touch_support_tickets_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_support_tickets_updated_at ON support_tickets;
CREATE TRIGGER trg_support_tickets_updated_at
  BEFORE UPDATE ON support_tickets
  FOR EACH ROW
  EXECUTE FUNCTION touch_support_tickets_updated_at();


ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;

-- Admins only. Ticket bodies contain customer PII (refund requests, account
-- numbers, etc.) — same posture as agent_runs.
DROP POLICY IF EXISTS support_tickets_select_admin ON support_tickets;
CREATE POLICY support_tickets_select_admin ON support_tickets
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS support_tickets_update_admin ON support_tickets;
CREATE POLICY support_tickets_update_admin ON support_tickets
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Inserts via service role only (the Inngest worker writes via SupportResponseAgent).

COMMENT ON TABLE support_tickets IS
  'Day 12: inbound emails classified as support/sales. AI draft lives in messages.';


-- ── Auto-tagger flag ────────────────────────────────────────────────────────
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS auto_tagged BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN documents.auto_tagged IS
  'Day 12: true once the auto-tagger has run and contributed at least one tag.';
