-- ── Customer Support Agent (fresh rebuild) ──────────────────────────────────
--
-- Replaces the retired SupportResponse agent (094_drop_support_response_agent
-- dropped its support_tickets table). This is a new design, not a restore:
--
--   * Per-org trust mode (shadow | assisted | autonomous) lives in
--     support_settings instead of a single global auto-send flag.
--   * The draft + its sources live in support_messages (append-only thread),
--     not denormalized directly on the ticket row, so regenerating a draft
--     or a human editing-then-sending both leave a clean history.
--   * agent_runs.agent_type for this feature is 'customer_support' — distinct
--     from the historical 'support_response' rows the old drop migration
--     intentionally left in place as audit history. No collision.
--
-- Schema choices mirror the codebase's established conventions (see
-- 029_agent_day7_agent_runs.sql, 032_agent_day12_support_and_autotag.sql):
-- free-text status/category (no CHECK) so new lifecycle states or
-- sub-categories don't need a migration; org_id denormalized onto child
-- tables so RLS policies never need a join; admin-only RLS posture because
-- ticket bodies carry customer PII.

-- ── support_tickets ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS support_tickets (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  channel                  TEXT NOT NULL DEFAULT 'email',   -- 'email' (v1); widget/slack later

  -- Threading key: sha256(from_email + normalized subject), normalized by
  -- stripping leading Re:/Fwd: prefixes. Non-unique on purpose — the agent's
  -- dedupe_or_create_ticket step looks up an existing non-terminal ticket
  -- with this key before inserting a new one; a resolved ticket that gets a
  -- new inbound message re-threading to the same key is a deliberate reopen,
  -- not a constraint violation.
  thread_key               TEXT NOT NULL,

  from_email               TEXT NOT NULL,
  from_name                TEXT,
  subject                  TEXT NOT NULL,

  -- Lifecycle: open -> pending_review -> resolved | escalated | spam
  --            pending_review -> awaiting_customer (reply sent, waiting)
  status                   TEXT NOT NULL DEFAULT 'open',

  category                 TEXT,                            -- triage sub-category (billing, how-to, bug, ...)
  priority                 TEXT,                             -- 'p0'..'p3'
  sentiment                TEXT,                             -- 'negative' | 'neutral' | 'positive'

  assigned_user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
  current_agent_run_id     UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  escalation_reason        TEXT,

  first_response_at        TIMESTAMPTZ,
  resolved_at               TIMESTAMPTZ,
  escalated_at              TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_org_created
  ON support_tickets(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_tickets_org_status_created
  ON support_tickets(org_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_support_tickets_org_thread
  ON support_tickets(org_id, thread_key);

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

-- Inserts via service role only (the Inngest worker writes via CustomerSupportAgent).

COMMENT ON TABLE support_tickets IS
  'Customer support agent (fresh rebuild): one row per customer conversation thread.';


-- ── support_messages ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS support_messages (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id                UUID NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  direction                TEXT NOT NULL,                   -- 'inbound' | 'outbound'
  author_type              TEXT NOT NULL,                   -- 'customer' | 'agent_draft' | 'human' | 'system'

  body                     TEXT NOT NULL,
  sources                  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{document_id, document_name, excerpt}, ...]
  confidence               REAL,                             -- 0..1, only set on author_type='agent_draft'

  -- Only meaningful for outbound rows: 'draft' | 'sent' | 'edited_and_sent' | 'rejected'
  status                   TEXT,
  sent_via                 TEXT,                             -- 'gmail' | 'manual'
  provider_message_id      TEXT,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_support_messages_ticket_created
  ON support_messages(ticket_id, created_at);

CREATE INDEX IF NOT EXISTS idx_support_messages_org_created
  ON support_messages(org_id, created_at DESC);

ALTER TABLE support_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS support_messages_select_admin ON support_messages;
CREATE POLICY support_messages_select_admin ON support_messages
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Admins can insert here directly (not just the service role): sending or
-- editing-then-sending a draft, and rejecting a draft with a note, are
-- actions an admin performs from the HTTP API, which per this codebase's
-- rule must go through a user-scoped (RLS-respecting) client, never
-- service_role. The agent's own writes (drafts) go through the same
-- policy too since Inngest workers use the service-role client, which
-- bypasses RLS entirely.
DROP POLICY IF EXISTS support_messages_insert_admin ON support_messages;
CREATE POLICY support_messages_insert_admin ON support_messages
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

COMMENT ON TABLE support_messages IS
  'Customer support agent (fresh rebuild): append-only thread per ticket, including AI drafts.';


-- ── support_settings ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS support_settings (
  org_id                   UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,

  enabled                  BOOLEAN NOT NULL DEFAULT FALSE,

  -- 'shadow' (draft logged only) | 'assisted' (draft shown, human sends,
  -- the default once enabled) | 'autonomous' (only for autonomous_categories,
  -- still subject to the hard sentiment/zero-source overrides enforced in
  -- code, not here).
  mode                     TEXT NOT NULL DEFAULT 'assisted'
                             CHECK (mode IN ('shadow', 'assisted', 'autonomous')),

  autonomous_categories    TEXT[] NOT NULL DEFAULT '{}',

  -- Gmail OAuth is per-user (gmail_integrations is keyed by org_id+user_id),
  -- not per-org — so "which connected mailbox do we send replies from" has
  -- to name a specific team member. NULL means no mailbox is wired up yet;
  -- the ticket UI falls back to draft-only + copy-to-clipboard.
  sender_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,

  tone                     TEXT,
  business_hours           JSONB,
  escalation_channel_id    TEXT,
  escalation_channel_name  TEXT,

  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION touch_support_settings_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_support_settings_updated_at ON support_settings;
CREATE TRIGGER trg_support_settings_updated_at
  BEFORE UPDATE ON support_settings
  FOR EACH ROW
  EXECUTE FUNCTION touch_support_settings_updated_at();

ALTER TABLE support_settings ENABLE ROW LEVEL SECURITY;

-- Full CRUD for admins on their own org's row — this is org configuration,
-- not agent-written audit data, so admins manage it directly via the API
-- using the user-scoped client (no service-role insert path needed).
DROP POLICY IF EXISTS support_settings_select_admin ON support_settings;
CREATE POLICY support_settings_select_admin ON support_settings
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS support_settings_insert_admin ON support_settings;
CREATE POLICY support_settings_insert_admin ON support_settings
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS support_settings_update_admin ON support_settings;
CREATE POLICY support_settings_update_admin ON support_settings
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

COMMENT ON TABLE support_settings IS
  'Customer support agent (fresh rebuild): one row per org — trust mode, autonomy allowlist, tone, escalation routing.';
