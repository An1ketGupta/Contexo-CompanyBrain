-- ── 073: Onboarding v2 — production hardening additions ─────────────────────
--
-- Adds five additive surfaces on top of migration 071 to take the pre-join
-- pipeline from "happy path works" to "ship to paying customers":
--
--   1. users.status — distinguishes pre_join (LOIsent, no login yet) from
--      active members. Lets the compliance UI surface "Pending Day-1" rows
--      and lets us scope analytics correctly. Default 'active' so existing
--      rows don't flip meaning.
--
--   2. onboarding_runs.pre_join_user_id, cancelled_at, cancellation_reason —
--      pointer to the auto-created Supabase auth row + cancel audit trail.
--
--   3. onboarding_documents.docusign_* — envelope tracking columns. We let
--      HR opt into DocuSign per-run; without DocuSign these stay NULL and
--      the agent falls back to plain-email-with-PDF.
--
--   4. organizations.logo_url, jurisdiction — branding for generated PDFs
--      and the jurisdiction string templates can reference.
--
--   5. onboarding_signing_envelopes — DocuSign envelope audit log (one row
--      per envelope created). We could put this on onboarding_documents
--      directly but envelope state (sent → delivered → completed → declined)
--      mutates often enough that a separate timeline-friendly table is
--      easier to query.
--
-- Idempotent — every statement re-runnable.

-- ── 1. users.status ────────────────────────────────────────────────────────
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_status_check;
ALTER TABLE users
  ADD CONSTRAINT users_status_check CHECK (
    status IN ('pre_join', 'active', 'suspended')
  );

CREATE INDEX IF NOT EXISTS idx_users_org_status
  ON users (org_id, status);

COMMENT ON COLUMN users.status IS
  'Lifecycle: pre_join (LOIsent, no first login yet) | active | suspended. '
  'OnboardingV2Agent creates pre_join rows at LOIsign time so policy '
  'acknowledgements can be assigned before Day 1; flips to active on first '
  'magic-link redemption.';


-- ── 2. onboarding_runs additions ───────────────────────────────────────────
ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS pre_join_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancellation_reason TEXT,
  ADD COLUMN IF NOT EXISTS cancelled_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_pre_join_user
  ON onboarding_runs (pre_join_user_id) WHERE pre_join_user_id IS NOT NULL;


-- ── 3. onboarding_documents — DocuSign envelope tracking ───────────────────
ALTER TABLE onboarding_documents
  ADD COLUMN IF NOT EXISTS docusign_envelope_id TEXT,
  ADD COLUMN IF NOT EXISTS docusign_status TEXT,
  ADD COLUMN IF NOT EXISTS docusign_signing_url TEXT,
  ADD COLUMN IF NOT EXISTS docusign_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS docusign_declined_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS docusign_decline_reason TEXT,
  ADD COLUMN IF NOT EXISTS used_default_template BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE onboarding_documents
  DROP CONSTRAINT IF EXISTS onboarding_documents_docusign_status_check;
ALTER TABLE onboarding_documents
  ADD CONSTRAINT onboarding_documents_docusign_status_check CHECK (
    docusign_status IS NULL OR docusign_status IN (
      'created', 'sent', 'delivered', 'signed',
      'completed', 'declined', 'voided', 'expired'
    )
  );

CREATE INDEX IF NOT EXISTS idx_onboarding_documents_docusign
  ON onboarding_documents (docusign_envelope_id)
  WHERE docusign_envelope_id IS NOT NULL;


-- ── 4. organizations — branding + jurisdiction ─────────────────────────────
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS logo_url TEXT,
  ADD COLUMN IF NOT EXISTS jurisdiction TEXT NOT NULL DEFAULT 'India',
  ADD COLUMN IF NOT EXISTS legal_name TEXT,
  ADD COLUMN IF NOT EXISTS registered_address TEXT;

COMMENT ON COLUMN organizations.jurisdiction IS
  'Default governing-law jurisdiction the LOI/AL/NDA templates reference. '
  'Customers override per-template via Jinja variables if needed.';
COMMENT ON COLUMN organizations.legal_name IS
  'Legal registered name of the company — used in contracts where the '
  'display name (organizations.name) is too informal.';


-- ── 5. onboarding_signing_envelopes — DocuSign envelope timeline ───────────
-- One row per envelope creation attempt. We keep this table separate from
-- onboarding_documents because:
--   * an envelope can wrap multiple documents (AL + NDA bundle = 1 envelope)
--   * we want to record webhook events as a timeline (created → sent →
--     delivered → completed) without overwriting prior state
--   * audit + compliance teams will export this independently
CREATE TABLE IF NOT EXISTS onboarding_signing_envelopes (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id            UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  run_id            UUID NOT NULL REFERENCES onboarding_runs(id) ON DELETE CASCADE,

  provider          TEXT NOT NULL DEFAULT 'docusign',
  envelope_id       TEXT NOT NULL,         -- DocuSign envelopeId
  -- The kinds in this envelope (e.g. ['appointment_letter', 'nda']).
  document_kinds    TEXT[] NOT NULL,

  status            TEXT NOT NULL DEFAULT 'created',

  recipient_email   TEXT NOT NULL,
  recipient_name    TEXT NOT NULL,
  signing_url       TEXT,                  -- embedded-signing URL (one-time)
  signing_url_expires_at TIMESTAMPTZ,

  sent_at           TIMESTAMPTZ,
  delivered_at      TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ,
  declined_at       TIMESTAMPTZ,
  voided_at         TIMESTAMPTZ,
  decline_reason    TEXT,

  -- Signed-PDF artifact returned by DocuSign on completion.
  signed_pdf_path   TEXT,

  -- Verbatim webhook events (most-recent-last) for forensic audit.
  events            JSONB NOT NULL DEFAULT '[]',

  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (provider, envelope_id)
);

ALTER TABLE onboarding_signing_envelopes
  DROP CONSTRAINT IF EXISTS onboarding_signing_envelopes_status_check;
ALTER TABLE onboarding_signing_envelopes
  ADD CONSTRAINT onboarding_signing_envelopes_status_check CHECK (
    status IN (
      'created', 'sent', 'delivered',
      'completed', 'declined', 'voided', 'expired'
    )
  );

CREATE INDEX IF NOT EXISTS idx_signing_envelopes_run
  ON onboarding_signing_envelopes (run_id);
CREATE INDEX IF NOT EXISTS idx_signing_envelopes_active
  ON onboarding_signing_envelopes (org_id, status, created_at DESC)
  WHERE status NOT IN ('completed', 'declined', 'voided', 'expired');

ALTER TABLE onboarding_signing_envelopes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS signing_envelopes_select ON onboarding_signing_envelopes;
CREATE POLICY signing_envelopes_select ON onboarding_signing_envelopes
  FOR SELECT USING (org_id = auth_org_id());

DROP TRIGGER IF EXISTS trg_signing_envelopes_updated_at ON onboarding_signing_envelopes;
CREATE TRIGGER trg_signing_envelopes_updated_at
  BEFORE UPDATE ON onboarding_signing_envelopes
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE onboarding_signing_envelopes IS
  'DocuSign (and future e-signature providers) envelope tracking. One row '
  'per envelope, may wrap multiple onboarding_documents kinds. Source of '
  'truth for "is the candidate done signing yet?" — supersedes the '
  'sign_status column on onboarding_documents when an envelope exists.';


-- ── 6. integrations — accept the docusign provider ────────────────────────
-- The unified integrations table (migration 036) has a CHECK constraint on
-- `provider`. Widen it to include 'docusign' so OAuth/API-key tokens for
-- DocuSign can be stored alongside the others.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_name = 'integrations'
      AND constraint_name = 'integrations_provider_check'
  ) THEN
    ALTER TABLE integrations DROP CONSTRAINT integrations_provider_check;
  END IF;
EXCEPTION WHEN OTHERS THEN
  -- Constraint name varies; if missing, skip silently.
  NULL;
END $$;

-- We intentionally do NOT add a replacement CHECK constraint with an
-- exhaustive list — the integrations table accepts arbitrary provider names
-- (validated at the application layer). Keeping it open avoids a migration
-- every time we add a provider.
