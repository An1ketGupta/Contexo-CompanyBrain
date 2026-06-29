-- ── 074: Onboarding v2 — LOI review-and-edit loop + candidate-driven references ──
--
-- Why this migration exists
-- -------------------------
-- Two product changes that ripple through the schema:
--
--   (a) HR wants to PREVIEW and EDIT the generated LOI before it's sent off for
--       signature. Today the agent goes straight from `loi_generating` to
--       `loi_pending_hr_sign` (emails HR immediately). The new flow inserts a
--       `loi_pending_hr_review` state where the agent parks, HR can download +
--       re-upload an edited .docx, and only on explicit approval does the
--       signature-request email fire.
--
--   (b) BGV references are collected from the CANDIDATE via a public form
--       (link embedded in the LOI email), NOT from HR at run-start. The agent
--       parks in `awaiting_candidate_references` until the candidate submits.
--       HR retains an override button to enter references manually if the
--       candidate ghosts.
--
-- Plus a template-upload draft-slot pattern so HR can iterate on a template
-- (upload → preview → edit → re-upload → preview) without disturbing the
-- currently-active template used by in-flight onboarding runs.
--
-- Five additive surfaces:
--
--   1. onboarding_runs.status — widen CHECK to include `loi_pending_hr_review`
--      and `awaiting_candidate_references`. Add `references_form_token`,
--      `references_form_expires_at`, `references_submitted_at`,
--      `loi_approved_for_signing_at` for timeline + form auth.
--
--   2. onboarding_documents — track HR's edited .docx separately from the
--      agent-generated original so we have an audit trail of the diff.
--
--   3. documents.template_status — 'draft' vs 'active'. The agent only
--      considers `active` templates. Uploading a new template (or re-uploading
--      after edits) keeps it `draft` until HR clicks Save Template.
--
--   4. organizations.required_references_count — per-org config (1–4, default
--      2) for how many BGV references the candidate must submit.
--
--   5. RLS unchanged — service-role mutations only, select policies inherited.
--
-- Idempotent — every statement re-runnable.

-- ── 1. onboarding_runs additions ──────────────────────────────────────────

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;
ALTER TABLE onboarding_runs
  ADD CONSTRAINT onboarding_runs_status_check CHECK (
    status IN (
      'draft',
      'loi_generating', 'loi_pending_hr_review', 'loi_pending_hr_sign',
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

ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS references_form_token UUID UNIQUE,
  ADD COLUMN IF NOT EXISTS references_form_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS references_submitted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS references_reminder_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS references_last_reminder_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS loi_approved_for_signing_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS loi_draft_edited_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS loi_draft_revision INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_refs_token
  ON onboarding_runs (references_form_token)
  WHERE references_form_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_onboarding_runs_awaiting_refs
  ON onboarding_runs (org_id, references_last_reminder_at)
  WHERE status = 'awaiting_candidate_references';

COMMENT ON COLUMN onboarding_runs.references_form_token IS
  'UUID embedded in the LOI-to-candidate email. Authorises the candidate to '
  'POST to /api/public/onboarding/references/{token} without a login.';
COMMENT ON COLUMN onboarding_runs.loi_approved_for_signing_at IS
  'Stamped when HR clicks "Send for signature" on the LOI review screen. '
  'Until then the agent is parked in loi_pending_hr_review and no signature '
  'email has been sent.';
COMMENT ON COLUMN onboarding_runs.loi_draft_revision IS
  'Incremented every time HR replaces the LOI draft with an edited .docx '
  'during the review step. Useful for the timeline + audit.';


-- ── 2. onboarding_documents — HR edit tracking ────────────────────────────
-- The agent generates the LOI from a template and stores it at
-- storage_path. If HR uploads an edited version during the review step,
-- we keep BOTH the original (storage_path) and the edit
-- (hr_edited_storage_path) for audit. The signature-request step prefers
-- hr_edited_storage_path when present; otherwise falls back to storage_path.

ALTER TABLE onboarding_documents
  ADD COLUMN IF NOT EXISTS hr_edited_storage_path TEXT,
  ADD COLUMN IF NOT EXISTS hr_edited_pdf_path TEXT,
  ADD COLUMN IF NOT EXISTS hr_edited_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS hr_edited_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS hr_edit_revision INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN onboarding_documents.hr_edited_storage_path IS
  'When HR uploads an edited .docx during loi_pending_hr_review, the file '
  'lands here. The agent stores it as-is (no further variable substitution) '
  '— HR has already seen filled values, their edits are final.';


-- ── 3. documents.template_status — draft vs active ────────────────────────
-- Template uploads land as `draft`. Only `active` templates are visible to
-- the OnboardingV2Agent's template lookup. Promotion is an explicit user
-- action ("Save template") so HR can iterate on a draft (re-upload edited
-- .docx) without disturbing in-flight runs that depend on the current
-- active template.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS template_status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_status_check;
ALTER TABLE documents
  ADD CONSTRAINT documents_template_status_check CHECK (
    template_status IN ('draft', 'active', 'archived')
  );

-- Filtered index — only the active templates are looked up at agent run time.
CREATE INDEX IF NOT EXISTS idx_documents_template_active
  ON documents (org_id, template_kind, created_at DESC)
  WHERE template_kind IS NOT NULL AND template_status = 'active';

COMMENT ON COLUMN documents.template_status IS
  'Lifecycle for template docs: draft (uploaded, being previewed/edited by '
  'HR — invisible to the Onboarding agent) | active (saved, in use) | '
  'archived (superseded by a newer active template). Non-template docs '
  '(template_kind IS NULL) default to active and the column is ignored.';


-- ── 4. organizations.required_references_count ────────────────────────────
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS required_references_count SMALLINT NOT NULL DEFAULT 2;

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_required_refs_check;
ALTER TABLE organizations
  ADD CONSTRAINT organizations_required_refs_check CHECK (
    required_references_count BETWEEN 1 AND 4
  );

COMMENT ON COLUMN organizations.required_references_count IS
  'How many BGV references the candidate must submit on their public '
  'references form before the agent advances to bgv_pending. 1–4, default 2.';
