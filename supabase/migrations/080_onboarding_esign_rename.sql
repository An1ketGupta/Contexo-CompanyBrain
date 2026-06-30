-- ── 080: Onboarding e-sign rename — DocuSign → DocuSeal cutover ─────────
--
-- We're replacing DocuSign with self-hosted DocuSeal (sign.nirnayaiq.com).
-- The `onboarding_signing_envelopes` table is already provider-agnostic
-- thanks to its `provider` column, but the per-document columns
-- (`docusign_envelope_id`, `docusign_status`, …) and the run status value
-- `loi_pending_docusign_signature` still name the provider. After the
-- cutover those names would be lies — the provider is DocuSeal, not DocuSign.
--
-- This migration renames those columns + status to neutral `esign_*` /
-- `loi_pending_esign_signature` so the schema doesn't lie about who's
-- handling the signature. Historical envelope rows that still have
-- `provider = 'docusign'` are left as-is for audit fidelity; only forward
-- naming gets corrected.
--
-- Idempotent: every ALTER/UPDATE is safe to re-run on a partially-applied
-- DB. Run after deploying the DocuSeal code, before flipping the
-- DOCUSEAL_* secrets in prod.

-- ── 1. onboarding_documents — rename DocuSign-specific columns ─────────────
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_envelope_id    TO esign_envelope_id;
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_status         TO esign_status;
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_signing_url    TO esign_signing_url;
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_completed_at   TO esign_completed_at;
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_declined_at    TO esign_declined_at;
ALTER TABLE onboarding_documents
  RENAME COLUMN docusign_decline_reason TO esign_decline_reason;

ALTER TABLE onboarding_documents
  DROP CONSTRAINT IF EXISTS onboarding_documents_docusign_status_check;
ALTER TABLE onboarding_documents
  ADD CONSTRAINT onboarding_documents_esign_status_check CHECK (
    esign_status IS NULL OR esign_status IN (
      'created', 'sent', 'delivered', 'signed',
      'completed', 'declined', 'voided', 'expired'
    )
  );

DROP INDEX IF EXISTS idx_onboarding_documents_docusign;
CREATE INDEX IF NOT EXISTS idx_onboarding_documents_esign
  ON onboarding_documents (esign_envelope_id)
  WHERE esign_envelope_id IS NOT NULL;

-- ── 2. onboarding_runs.status — rename the in-flight value ─────────────────
-- Backfill any rows (likely zero in prod) before we tighten the CHECK.
UPDATE onboarding_runs
   SET status = 'loi_pending_esign_signature'
 WHERE status = 'loi_pending_docusign_signature';

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;
ALTER TABLE onboarding_runs
  ADD CONSTRAINT onboarding_runs_status_check CHECK (
    status IN (
      'draft',
      'loi_generating', 'loi_pending_hr_review', 'loi_pending_hr_sign',
      'loi_pending_esign_signature',
      'loi_signed_uploaded', 'loi_sent_to_candidate',
      'awaiting_candidate_references',
      'bgv_pending', 'bgv_complete',
      'appointment_bundle_generating', 'appointment_pending_hr_review',
      'appointment_sent_to_candidate',
      'policies_assigned', 'policies_acknowledged',
      'induction_generating', 'induction_sent',
      'completed',
      'blocked_missing_template', 'failed', 'cancelled'
    )
  );

-- ── 3. onboarding_signing_envelopes.provider — new default ─────────────────
-- All new envelopes go through DocuSeal. Existing rows with
-- provider='docusign' stay as they are — they're historical truth.
ALTER TABLE onboarding_signing_envelopes
  ALTER COLUMN provider SET DEFAULT 'docuseal';

COMMENT ON COLUMN onboarding_signing_envelopes.provider IS
  'E-sign provider that issued this envelope. Today: docuseal (default). '
  'Historical rows from before 2026-07 may have provider=docusign; those '
  'envelopes are read-only and the DocuSign code path has been removed.';
