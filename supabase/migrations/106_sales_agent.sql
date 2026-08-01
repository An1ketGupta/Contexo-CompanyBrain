-- ── Sales Outreach Agent ─────────────────────────────────────────────────────
--
-- Outbound counterpart to the customer support agent (095). Same shape,
-- opposite direction: support drafts replies to mail that arrives, sales
-- drafts mail that goes out to a list the org supplied.
--
-- Deliberate v1 scoping decisions encoded here:
--
--   * Leads are bring-your-own. There is no discovery/prospecting source —
--     `source` exists to record how a row got here ('csv_import' today,
--     'crm_sync' when a CRM integration lands) but nothing populates these
--     tables except an admin import.
--   * These tables ARE the CRM for v1. No HubSpot/Salesforce integration
--     exists in the codebase, so `sales_leads` is the system of record for
--     stage and `bant` rather than a mirror of an external one.
--   * Autonomy is gated by message kind in code, not by an allowlist column.
--     Support has `autonomous_categories` because its topics are open-ended;
--     here cold first-touch and anything mentioning price is never
--     autonomous regardless of settings, so there is nothing to configure.
--
-- Conventions mirror 095_customer_support_agent.sql: free-text status (no
-- CHECK) so new lifecycle states don't need a migration, org_id denormalized
-- onto child tables so RLS policies never join, admin-only RLS because lead
-- rows carry named contacts' business contact details.

-- ── sales_leads ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sales_leads (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  company_name             TEXT NOT NULL,
  domain                   TEXT,

  contact_name             TEXT,
  -- Normalized to lowercase at write time by the import endpoint. Stored
  -- plain (rather than relying on a lower() expression index) so PostgREST's
  -- `on_conflict` can target the unique constraint below by column name.
  contact_email            TEXT NOT NULL,
  contact_title            TEXT,

  -- How this row arrived. 'csv_import' is the only producer in v1.
  source                   TEXT NOT NULL DEFAULT 'csv_import',

  -- Free-text notes the importer supplied (a CSV column), fed to the drafting
  -- prompt as operator-provided context. This is the only prospect-specific
  -- information the agent has in v1 — it does no external research.
  context_note             TEXT,

  -- Lifecycle:
  --   new -> shadow_drafted | pending_review | awaiting_reply
  --   awaiting_reply -> replied -> pending_review (reply draft) -> ...
  --   -> qualified -> meeting_booked -> won | lost
  --   any -> escalated (agent couldn't produce a sendable draft)
  status                   TEXT NOT NULL DEFAULT 'new',

  -- Qualification signals extracted from replies (budget/authority/need/
  -- timeline). Written by a human in v1; the agent reads it as draft context.
  bant                     JSONB NOT NULL DEFAULT '{}'::jsonb,

  assigned_user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
  current_agent_run_id     UUID REFERENCES agent_runs(id) ON DELETE SET NULL,

  -- Cadence bookkeeping. next_follow_up_at is set when an outbound send
  -- succeeds and cleared when the prospect replies; the daily cron scans it.
  next_follow_up_at        TIMESTAMPTZ,
  follow_up_count          INTEGER NOT NULL DEFAULT 0,

  escalation_reason        TEXT,

  first_contacted_at       TIMESTAMPTZ,
  last_contacted_at        TIMESTAMPTZ,
  replied_at               TIMESTAMPTZ,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One lead per contact per org. Re-importing the same CSV is a common
-- operator mistake; this turns it into a no-op upsert instead of a duplicate
-- outreach thread to the same person.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sales_leads_org_email
  ON sales_leads(org_id, contact_email);

CREATE INDEX IF NOT EXISTS idx_sales_leads_org_created
  ON sales_leads(org_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sales_leads_org_status_created
  ON sales_leads(org_id, status, created_at DESC);

-- Drives the daily cadence cron: "which leads are due a follow-up".
-- Partial — only rows actually awaiting one are ever scanned.
CREATE INDEX IF NOT EXISTS idx_sales_leads_due_followup
  ON sales_leads(next_follow_up_at)
  WHERE next_follow_up_at IS NOT NULL;

CREATE OR REPLACE FUNCTION touch_sales_leads_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sales_leads_updated_at ON sales_leads;
CREATE TRIGGER trg_sales_leads_updated_at
  BEFORE UPDATE ON sales_leads
  FOR EACH ROW
  EXECUTE FUNCTION touch_sales_leads_updated_at();

ALTER TABLE sales_leads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sales_leads_select_admin ON sales_leads;
CREATE POLICY sales_leads_select_admin ON sales_leads
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Admins insert directly: CSV import is an admin HTTP action and therefore
-- goes through the user-scoped (RLS-respecting) client, never service_role.
DROP POLICY IF EXISTS sales_leads_insert_admin ON sales_leads;
CREATE POLICY sales_leads_insert_admin ON sales_leads
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS sales_leads_update_admin ON sales_leads;
CREATE POLICY sales_leads_update_admin ON sales_leads
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS sales_leads_delete_admin ON sales_leads;
CREATE POLICY sales_leads_delete_admin ON sales_leads
  FOR DELETE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

COMMENT ON TABLE sales_leads IS
  'Sales outreach agent: one row per prospect contact, imported by an admin. System of record for outreach stage in v1 (no external CRM).';


-- ── sales_messages ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sales_messages (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lead_id                  UUID NOT NULL REFERENCES sales_leads(id) ON DELETE CASCADE,
  org_id                   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

  direction                TEXT NOT NULL,                   -- 'outbound' | 'inbound'
  author_type              TEXT NOT NULL,                   -- 'agent_draft' | 'human' | 'prospect' | 'system'

  -- Which prompt produced this draft: 'first_touch' | 'follow_up' | 'reply'.
  -- Routing reads it — first_touch is never eligible for autonomous send.
  kind                     TEXT,

  subject                  TEXT,
  body                     TEXT NOT NULL,
  sources                  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{document_id, document_name, excerpt}, ...]
  confidence               REAL,                             -- 0..1, only set on author_type='agent_draft'

  -- Outbound only: 'draft' | 'sent' | 'edited_and_sent' | 'rejected'
  status                   TEXT,
  sent_via                 TEXT,                             -- 'gmail'
  provider_message_id      TEXT,

  created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sales_messages_lead_created
  ON sales_messages(lead_id, created_at);

CREATE INDEX IF NOT EXISTS idx_sales_messages_org_created
  ON sales_messages(org_id, created_at DESC);

ALTER TABLE sales_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sales_messages_select_admin ON sales_messages;
CREATE POLICY sales_messages_select_admin ON sales_messages
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

-- Admins insert here too: approving/editing-then-sending a draft, and logging
-- a reply the prospect sent, are admin HTTP actions on the user-scoped client.
-- The agent's own drafts are written by the Inngest worker via service_role,
-- which bypasses RLS entirely.
DROP POLICY IF EXISTS sales_messages_insert_admin ON sales_messages;
CREATE POLICY sales_messages_insert_admin ON sales_messages
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS sales_messages_update_admin ON sales_messages;
CREATE POLICY sales_messages_update_admin ON sales_messages
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

COMMENT ON TABLE sales_messages IS
  'Sales outreach agent: append-only thread per lead, including AI drafts and their retrieval sources.';


-- ── sales_settings ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sales_settings (
  org_id                   UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,

  enabled                  BOOLEAN NOT NULL DEFAULT FALSE,

  -- 'shadow'     — draft and record it, queue nothing, notify nobody. For
  --                benchmarking the agent's output before trusting it.
  -- 'assisted'   — every draft waits for a human to approve. The default.
  -- 'autonomous' — follow-up nudges may send themselves. Cold first-touch
  --                and anything mentioning price still cannot; that override
  --                lives in code and no setting here can lift it.
  mode                     TEXT NOT NULL DEFAULT 'assisted'
                             CHECK (mode IN ('shadow', 'assisted', 'autonomous')),

  -- Gmail OAuth is per-user (gmail_integrations is keyed org_id+user_id), so
  -- outbound mail has to go from a specific rep's connected mailbox. NULL
  -- means nothing can send and every draft stays draft-only.
  sender_user_id           UUID REFERENCES users(id) ON DELETE SET NULL,

  -- Voice/tone instruction appended to the drafting prompt.
  tone                     TEXT,

  -- Days after a send before the next follow-up is due.
  follow_up_delay_days     INTEGER NOT NULL DEFAULT 3
                             CHECK (follow_up_delay_days BETWEEN 1 AND 60),

  -- Give up after this many unanswered follow-ups and mark the lead lost.
  max_follow_ups           INTEGER NOT NULL DEFAULT 3
                             CHECK (max_follow_ups BETWEEN 0 AND 10),

  -- Ceiling on how many leads the daily cadence cron will process for this
  -- org in one run. Protects both the LLM spend and the sending domain's
  -- reputation from a 5,000-row CSV import.
  daily_send_cap           INTEGER NOT NULL DEFAULT 20
                             CHECK (daily_send_cap BETWEEN 1 AND 500),

  escalation_channel_id    TEXT,
  escalation_channel_name  TEXT,

  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION touch_sales_settings_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sales_settings_updated_at ON sales_settings;
CREATE TRIGGER trg_sales_settings_updated_at
  BEFORE UPDATE ON sales_settings
  FOR EACH ROW
  EXECUTE FUNCTION touch_sales_settings_updated_at();

ALTER TABLE sales_settings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sales_settings_select_admin ON sales_settings;
CREATE POLICY sales_settings_select_admin ON sales_settings
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS sales_settings_insert_admin ON sales_settings;
CREATE POLICY sales_settings_insert_admin ON sales_settings
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

DROP POLICY IF EXISTS sales_settings_update_admin ON sales_settings;
CREATE POLICY sales_settings_update_admin ON sales_settings
  FOR UPDATE USING (
    org_id = auth_org_id()
    AND EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid() AND users.role = 'admin')
  );

COMMENT ON TABLE sales_settings IS
  'Sales outreach agent: one row per org — trust mode, sending mailbox, follow-up cadence, daily cap, escalation routing.';
