-- Team invitations.
--
-- Lifecycle:
--   1. Admin POSTs /organizations/invitations → row inserted with a secure
--      token (generated server-side via secrets.token_urlsafe(32)) and
--      expires_at = now() + 7 days.
--   2. Resend sends the invitee a link to /auth/accept-invite?token=...
--   3. Invitee lands on the page, signs up, then POSTs /auth/invitations/{token}/accept
--      which atomically (a) marks accepted_at, (b) inserts the users row with
--      the invite's org_id + role.
--   4. Admin can DELETE any row to revoke (hard delete is fine — accepted
--      rows are kept for audit until we ship audit logs separately).
--
-- We use a partial unique index on (org_id, lower(email)) WHERE accepted_at IS NULL
-- AND expires_at > now() so the same admin can't double-invite, but a
-- previously-expired or accepted invite for the same email doesn't block a
-- fresh send.

CREATE TABLE invitations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
  token       TEXT NOT NULL UNIQUE,
  invited_by  UUID REFERENCES users(id) ON DELETE SET NULL,
  expires_at  TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '7 days'),
  accepted_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Token is the hot lookup path on the accept flow — lone index, NOT included
-- in the PK since it's a long random string and the PK should stay a UUID.
CREATE INDEX idx_invitations_token ON invitations (token);

-- Pending-invitations list for the Team tab.
CREATE INDEX idx_invitations_org_pending
  ON invitations (org_id, created_at DESC)
  WHERE accepted_at IS NULL;

-- Prevent duplicate active invites for the same email/org.
CREATE UNIQUE INDEX idx_invitations_unique_active
  ON invitations (org_id, lower(email))
  WHERE accepted_at IS NULL AND expires_at > now();

ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- Members of an org can see pending invites (for the Team UI), only admins can
-- create/revoke. Token-based lookups during the accept flow bypass RLS and run
-- as the Next.js service role — see app/api/auth/invitations/[token]/route.ts.

CREATE POLICY "invitations_select" ON invitations
  FOR SELECT USING (org_id = auth_org_id());

CREATE POLICY "invitations_insert" ON invitations
  FOR INSERT WITH CHECK (
    org_id = auth_org_id()
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
  );

CREATE POLICY "invitations_delete" ON invitations
  FOR DELETE USING (
    org_id = auth_org_id()
    AND (SELECT role FROM public.users WHERE id = auth.uid()) = 'admin'
  );

-- No general UPDATE policy. The accept flow updates accepted_at via the
-- service-role client (token bearer is unauthenticated at that point).
