-- ── V5 Day 3 — Gmail per-user OAuth + delivery_status on messages
--
-- Why per-user (not per-org like drive_integrations): Gmail Send writes from a
-- specific person's mailbox. If we shared one org-level token, every outbound
-- email would appear to come from whichever admin happened to authorize Drive
-- — wrong sender identity, no per-user audit trail. So Gmail OAuth is a
-- separate flow keyed (org_id, user_id). Drive stays org-level.

-- ── 1. gmail_integrations ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gmail_integrations (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  -- The Gmail address derived from the id_token / userinfo endpoint at
  -- connect time. Used purely for display ("Sending as alice@acme.com").
  email_address    TEXT NOT NULL,
  -- Mirror of the issued OAuth tokens. Stored plaintext like drive_integrations
  -- (Supabase at-rest encryption + RLS lockdown). Rotate on disconnect.
  access_token     TEXT NOT NULL,
  refresh_token    TEXT NOT NULL,
  token_expiry     TIMESTAMPTZ,
  -- Full scope list returned by Google. We re-check has_send_scope on the
  -- read path rather than trusting a stale boolean — but keep the granted
  -- list here for the integration card to render ("Drive read, Gmail send").
  scopes           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  connected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at     TIMESTAMPTZ,
  CONSTRAINT gmail_integrations_unique_user UNIQUE (org_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_gmail_integrations_org ON gmail_integrations(org_id);
CREATE INDEX IF NOT EXISTS idx_gmail_integrations_user ON gmail_integrations(user_id);

ALTER TABLE gmail_integrations ENABLE ROW LEVEL SECURITY;

-- A user can read & manage only their own Gmail row. Admins do NOT get a
-- back-door read; tokens belong to the user who issued them. Sends always
-- happen through the FastAPI service role anyway (via Inngest worker), so
-- the RLS read scope is purely a defense-in-depth for the dashboard.
DROP POLICY IF EXISTS gmail_integrations_select_self ON gmail_integrations;
CREATE POLICY gmail_integrations_select_self ON gmail_integrations
  FOR SELECT USING (user_id = auth.uid() AND org_id = auth_org_id());

DROP POLICY IF EXISTS gmail_integrations_delete_self ON gmail_integrations;
CREATE POLICY gmail_integrations_delete_self ON gmail_integrations
  FOR DELETE USING (user_id = auth.uid() AND org_id = auth_org_id());

-- ── 2. messages.delivery_status ────────────────────────────────────────────
-- JSONB shape: {
--   "channel": "gmail" | "slack" | "notion",
--   "status":  "queued" | "sent" | "failed",
--   "recipient": "alice@acme.com",        -- gmail
--   "channel_name": "#announcements",      -- slack
--   "job_id": "<uuid>",
--   "queued_at": iso8601,
--   "delivered_at": iso8601 | null,
--   "external_id": "<gmail_message_id|slack_ts|notion_page_id>",
--   "error": "..." | null
-- }
-- One delivery per message for now — a single AI output can only be sent
-- once. If we ever support multi-channel fan-out, this becomes JSONB[] or a
-- dedicated `message_deliveries` table.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS delivery_status JSONB;

-- We query "show me unsent task outputs" + "show me a user's send history",
-- both filtered by the channel field. GIN on the JSONB keeps that fast.
CREATE INDEX IF NOT EXISTS idx_messages_delivery_status
  ON messages USING GIN (delivery_status jsonb_path_ops)
  WHERE delivery_status IS NOT NULL;
