-- ── 112: Document collection stops requiring a login ───────────────────────
--
-- Why this migration exists
-- -------------------------
-- A `collect` step emails the candidate "we need a few documents" with a link
-- to /candidate/onboarding — a page inside the dashboard route group. Reaching
-- it means having a session, which means finding the magic-link email that was
-- sent at run creation, days or weeks earlier, and which is the email people
-- most reliably lose. The document ask is often the very first thing a
-- candidate is asked to do, and it was gated behind the one credential they
-- were least likely to still have.
--
-- The reference forms already solved this: the credential lives in the URL as a
-- random UUID with an expiry (074, `references_form_token`). This does the same
-- for document collection, and for the same reason — the recipient is outside
-- the org and should not need an account to hand something in.
--
-- Run-scoped rather than step-scoped on purpose. A run can have several collect
-- steps at different points in the pipeline; one link that always shows
-- whatever has been asked for so far means the candidate can bookmark it, and a
-- later ask doesn't strand them on a link that only knew about the first one.
-- The expiry is pushed forward each time a new ask goes out.
--
-- Idempotent — every statement re-runnable.

ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS documents_token UUID UNIQUE,
  ADD COLUMN IF NOT EXISTS documents_token_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_documents_token
  ON onboarding_runs (documents_token)
  WHERE documents_token IS NOT NULL;

COMMENT ON COLUMN onboarding_runs.documents_token IS
  'UUID embedded in the "we need a few documents" email. Authorises the '
  'candidate to read their checklist and upload against it at '
  '/onboarding/public/documents/{token} without a login. Rotated only by '
  'minting a new one — the column is unique, so a stale link 404s.';

COMMENT ON COLUMN onboarding_runs.documents_token_expires_at IS
  'Enforced on read and on upload. Pushed forward whenever a new collect step '
  'asks the candidate for something, so a run whose second document request '
  'lands a month later does not hand out a dead link.';
