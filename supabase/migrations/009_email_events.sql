-- Transactional email ledger.
--
-- We record one row per successful Resend send so the Inngest worker can
-- guarantee at-most-once delivery in the face of retries. The dedupe_key
-- is what separates events that are inherently one-shot (welcome) from
-- events that recur per resource (document_ready, quota_warning):
--
--   invite           dedupe_key = invitation_id
--   welcome          dedupe_key = NULL  (one per user lifetime)
--   document_ready   dedupe_key = document_id
--   quota_warning    dedupe_key = YYYY-MM  (once per user per month)
--   quota_exceeded   dedupe_key = YYYY-MM
--   weekly_digest    dedupe_key = YYYY-WW  (ISO week)
--
-- The partial unique index handles the NULL-vs-value asymmetry: rows with a
-- non-null dedupe_key dedupe on (user_id, event_type, dedupe_key), while
-- rows with NULL dedupe on (user_id, event_type) alone. Without the split
-- index, NULLs would all be considered distinct from each other and welcome
-- emails could double-send.

CREATE TABLE email_events (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      UUID REFERENCES organizations(id) ON DELETE CASCADE,
  user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
  event_type  TEXT NOT NULL,
  recipient   TEXT NOT NULL,
  dedupe_key  TEXT,
  resend_id   TEXT,
  metadata    JSONB NOT NULL DEFAULT '{}',
  sent_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Recipient-based lookups (for "who has received what" reports).
CREATE INDEX idx_email_events_recipient ON email_events (recipient, event_type);

-- Org listing (admin dashboards down the line).
CREATE INDEX idx_email_events_org ON email_events (org_id, sent_at DESC)
  WHERE org_id IS NOT NULL;

-- Idempotency: send-with-key. Allows the worker to upsert safely.
CREATE UNIQUE INDEX idx_email_events_keyed
  ON email_events (user_id, event_type, dedupe_key)
  WHERE user_id IS NOT NULL AND dedupe_key IS NOT NULL;

-- Idempotency for invitations (user_id is NULL pre-acceptance) — dedupe on
-- (recipient, event_type, dedupe_key).
CREATE UNIQUE INDEX idx_email_events_anon_keyed
  ON email_events (recipient, event_type, dedupe_key)
  WHERE user_id IS NULL AND dedupe_key IS NOT NULL;

-- Idempotency: send-without-key (welcome). One per user lifetime.
CREATE UNIQUE INDEX idx_email_events_unkeyed
  ON email_events (user_id, event_type)
  WHERE user_id IS NOT NULL AND dedupe_key IS NULL;

ALTER TABLE email_events ENABLE ROW LEVEL SECURITY;

-- No user-facing access. Only the service role writes/reads.
-- (No SELECT/INSERT/UPDATE/DELETE policies = effectively locked out.)
