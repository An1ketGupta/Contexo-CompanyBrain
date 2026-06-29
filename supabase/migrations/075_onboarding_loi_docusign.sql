-- 075_onboarding_loi_docusign.sql
-- LOI signing via DocuSign (HR → candidate routed).
--
-- Why:
--   Until now the LOI flow required HR to print/scan/upload, then the
--   candidate received the LOI as a PDF attachment but didn't sign it
--   digitally. When DocuSign is configured we want a single envelope routed
--   from HR to the candidate, with both signatures captured electronically.
--
-- Changes:
--   1. onboarding_runs.status — add `loi_pending_docusign_signature` to the
--      allowed values. The agent parks the run there while the envelope is
--      in flight; the webhook flips it to `loi_signed_uploaded` on
--      completion so the existing _step_send_loi_to_candidate kicks in.
--
--   2. onboarding_signing_envelopes.signers — new JSONB column storing the
--      per-signer state for routed envelopes (role, email, name, status,
--      signing_url, etc.). The existing recipient_email/name keep tracking
--      the FIRST signer for backward compat with the AL+NDA single-signer
--      envelopes already in production.
--
-- Idempotent — every statement re-runnable.

-- ── 1. Widen onboarding_runs.status CHECK ──────────────────────────────────

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


-- ── 2. Per-signer JSONB on the envelopes table ─────────────────────────────

ALTER TABLE onboarding_signing_envelopes
  ADD COLUMN IF NOT EXISTS signers JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN onboarding_signing_envelopes.signers IS
  'Per-signer state for routed envelopes. Array of '
  '{role, email, name, routing_order, recipient_id, client_user_id, '
  'status, signing_url, completed_at, declined_at, decline_reason}. '
  'Single-signer envelopes (AL+NDA today) leave this as the default empty '
  'array — recipient_email/recipient_name still carry the only signer.';
