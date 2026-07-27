-- ── Support mailbox: the org's real inbound support address
--
-- The customer support agent previously only received mail forwarded to a
-- synthetic address (organizations.inbound_email), which no customer would
-- write to organically. An org can now connect its actual support mailbox
-- (support@theircompany.com) over OAuth; a cron polls it for new customer
-- email and replies go back out from that same address so threads hold
-- together.
--
-- Deliberately NOT gmail_integrations: that table is per-(org,user) and
-- exists so each teammate can send AI-drafted mail as themselves from chat.
-- The support mailbox is org-level infrastructure — exactly one per org,
-- connected by an admin, shared by the whole team — and needs read scope
-- that no individual's personal mailbox should be asked for.

CREATE TABLE IF NOT EXISTS support_mailboxes (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  -- UNIQUE, not just indexed: one support inbox per org. Two would mean
  -- ambiguity about which address replies come from.
  org_id          UUID NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
  connected_by    UUID REFERENCES users(id) ON DELETE SET NULL,
  -- Resolved from the userinfo endpoint at connect time. This is the address
  -- customers write to and the From on every reply.
  email_address   TEXT NOT NULL,
  -- Stored like drive_integrations / gmail_integrations: Supabase at-rest
  -- encryption plus RLS lockdown, never returned over the API surface.
  access_token    TEXT NOT NULL,
  refresh_token   TEXT NOT NULL,
  token_expiry    TIMESTAMPTZ,
  -- Full granted scope list. Re-checked on the read path rather than trusting
  -- a stale boolean: a mailbox missing gmail.readonly can't be polled, and one
  -- missing gmail.send can't reply.
  scopes          TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  -- Gmail History API cursor. NULL = not yet bootstrapped; the first poll
  -- records the mailbox's current historyId and deliberately ingests nothing,
  -- so connecting an inbox doesn't backfill years of mail as tickets. TEXT,
  -- not BIGINT: Google documents historyId as an opaque monotonic token.
  history_id      TEXT,
  last_polled_at  TIMESTAMPTZ,
  connected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_support_mailboxes_org ON support_mailboxes(org_id);

ALTER TABLE support_mailboxes ENABLE ROW LEVEL SECURITY;

-- Admins-only on read so token material doesn't leak to org members, matching
-- drive_integrations. Writes happen through the service role in the OAuth
-- callback and the polling cron, so no INSERT/UPDATE policy is needed.
DROP POLICY IF EXISTS support_mailboxes_select_admin ON support_mailboxes;
CREATE POLICY support_mailboxes_select_admin ON support_mailboxes
  FOR SELECT USING (
    org_id = auth_org_id()
    AND EXISTS (
      SELECT 1 FROM users
      WHERE users.id = auth.uid() AND users.role = 'admin'
    )
  );

COMMENT ON TABLE support_mailboxes IS
  'Org-level Gmail mailbox polled for inbound customer support email and used as the From on replies. One per org.';
