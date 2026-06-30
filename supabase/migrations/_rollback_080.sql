-- ── ROLLBACK for migration 080 — DO NOT auto-apply ─────────────────────────
--
-- This file is parked (underscore prefix) so Supabase CLI does NOT auto-run
-- it. Apply manually via `supabase db push --file _rollback_080.sql` ONLY
-- if the DocuSeal cutover needs to be reversed and you've already reverted
-- the application code that uses `esign_*` column names.
--
-- After running this you must also redeploy with the pre-cutover code so
-- callers reference `docusign_*` again.

-- 1. Revert onboarding_documents columns.
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_envelope_id    TO docusign_envelope_id;
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_status         TO docusign_status;
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_signing_url    TO docusign_signing_url;
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_completed_at   TO docusign_completed_at;
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_declined_at    TO docusign_declined_at;
ALTER TABLE onboarding_documents
  RENAME COLUMN esign_decline_reason TO docusign_decline_reason;

ALTER TABLE onboarding_documents
  DROP CONSTRAINT IF EXISTS onboarding_documents_esign_status_check;
ALTER TABLE onboarding_documents
  ADD CONSTRAINT onboarding_documents_docusign_status_check CHECK (
    docusign_status IS NULL OR docusign_status IN (
      'created', 'sent', 'delivered', 'signed',
      'completed', 'declined', 'voided', 'expired'
    )
  );

DROP INDEX IF EXISTS idx_onboarding_documents_esign;
CREATE INDEX IF NOT EXISTS idx_onboarding_documents_docusign
  ON onboarding_documents (docusign_envelope_id)
  WHERE docusign_envelope_id IS NOT NULL;

-- 2. Revert run status backfill + CHECK.
UPDATE onboarding_runs
   SET status = 'loi_pending_docusign_signature'
 WHERE status = 'loi_pending_esign_signature';

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;
ALTER TABLE onboarding_runs
  ADD CONSTRAINT onboarding_runs_status_check CHECK (
    status IN (
      'draft',
      'loi_generating', 'loi_pending_hr_review', 'loi_pending_hr_sign',
      'loi_pending_docusign_signature',
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

-- 3. Revert envelopes default.
ALTER TABLE onboarding_signing_envelopes
  ALTER COLUMN provider SET DEFAULT 'docusign';
