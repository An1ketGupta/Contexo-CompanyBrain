-- ── Agent Roadmap Day 6 — Human-in-the-Loop Approval Workflow
--
-- A user submits any AI output for review. An approver decides via:
--   1. /approvals page inside the app (logged in)
--   2. One-click magic-link from email (HMAC-signed token in URL)
--   3. Approve/Reject buttons in a Slack DM (Block Kit)
--
-- On approve, the stored `execution_action` is fanned out to the same Inngest
-- functions that already handle outbound writes (gmail/send-email,
-- slack/post-message, notion/create-page, gdocs/create-doc).
--
-- Schema choices:
--   * `token_hash` is sha256(secret || token); we never store the plaintext
--     token. Email links contain the plaintext; the server hashes on lookup.
--   * `slack_message` { channel, ts } lets the interactions handler do
--     chat.update so the Slack DM mutates in place after approval.
--   * `audit` JSONB captures the resolution provenance (UA, IP, channel)
--     because the resolution path can be web/email/slack.
--   * `execution_action` JSONB shape:
--         { "channel": "gmail" | "slack" | "notion" | "gdocs",
--           "params": { ... channel-specific fields used by the existing
--                       inngest events } }
--   * One approval per (message_id, approver_id) WHERE status = 'pending'
--     to prevent duplicate inboxes when a requester double-submits.

CREATE TABLE IF NOT EXISTS approvals (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  message_id           UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,

  requested_by         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  approver_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  status               TEXT NOT NULL DEFAULT 'pending',
  note                 TEXT,
  preview_text         TEXT,

  -- Channel-specific params to execute on approval. Validated at the FastAPI
  -- layer; the worker treats this as a typed enum on `channel`.
  execution_action     JSONB NOT NULL,

  -- Magic-link auth. Plaintext token lives only in the email URL. We store
  -- the sha256 hash + expiry so a leaked DB doesn't grant approval rights.
  token_hash           TEXT NOT NULL,
  token_expires_at     TIMESTAMPTZ NOT NULL,

  -- Slack DM coords so the buttons can update the message in place.
  slack_message        JSONB,

  -- Provenance of the resolution. Set on resolve, never updated thereafter.
  resolved_via         TEXT,   -- 'web' | 'email' | 'slack'
  resolved_audit       JSONB,  -- { ip, ua, ts }

  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at          TIMESTAMPTZ,
  reminder_sent_at     TIMESTAMPTZ,

  CONSTRAINT approvals_status_check
    CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'execution_failed', 'expired')),
  CONSTRAINT approvals_resolved_via_check
    CHECK (resolved_via IS NULL OR resolved_via IN ('web', 'email', 'slack')),
  CONSTRAINT approvals_channel_present
    CHECK (execution_action ? 'channel'),
  CONSTRAINT approvals_not_self
    CHECK (requested_by <> approver_id)
);

-- One open approval per (message, approver) — prevents flooding the
-- approver if a requester double-clicks Submit.
CREATE UNIQUE INDEX IF NOT EXISTS uq_approvals_pending_per_message_approver
  ON approvals(message_id, approver_id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_approvals_org_status_created
  ON approvals(org_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_approvals_approver_status
  ON approvals(approver_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_approvals_requester_status
  ON approvals(requested_by, status, created_at DESC);

-- Magic-link lookup hits this — token_hash is unique-enough that we don't
-- need a unique constraint, but we DO want a covering index.
CREATE INDEX IF NOT EXISTS idx_approvals_token_hash
  ON approvals(token_hash);


ALTER TABLE approvals ENABLE ROW LEVEL SECURITY;

-- Reads: requester or approver in the same org. Admins also see everything
-- for the org so they can audit who's gating what.
DROP POLICY IF EXISTS approvals_select_participant ON approvals;
CREATE POLICY approvals_select_participant ON approvals
  FOR SELECT USING (
    org_id = auth_org_id()
    AND (
      requested_by = auth.uid()
      OR approver_id = auth.uid()
      OR EXISTS (
        SELECT 1 FROM users
        WHERE users.id = auth.uid() AND users.role = 'admin'
      )
    )
  );

-- Writes happen exclusively through service-role (the FastAPI endpoints
-- check plan + role themselves). No general INSERT/UPDATE policy.

COMMENT ON TABLE approvals IS
  'Day 6: Human-in-the-loop approval queue. Approvers resolve via web, signed email link, or Slack buttons.';
COMMENT ON COLUMN approvals.token_hash IS
  'sha256 of (internal_email_secret || raw_token). Plaintext lives only in the email URL.';
COMMENT ON COLUMN approvals.execution_action IS
  '{ channel: gmail|slack|notion|gdocs, params: {...} } — fanned out on approve.';
