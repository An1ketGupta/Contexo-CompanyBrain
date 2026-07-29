-- ── 100: Drop the legacy onboarding template pipeline ─────────────────────
--
-- Cutover migration. Apply this only AFTER deploying the code that removes the
-- last readers of these columns — the template endpoints in
-- routers/onboarding_v2.py and the modules under
-- services/agents/onboarding_v2 that backed them. Both are gone in the same
-- change as this file.
--
-- What replaced them
-- ------------------
-- Migration 099 introduced templates as a first-class entity:
--
--   documents.template_kind / template_status  ->  doc_templates
--                                                  + document_types
--   (no equivalent)                            ->  doc_template_versions
--   template_field_slots.variable (TEXT)       ->  doc_template_variables
--                                                  (typed, validated, defaulted)
--   template_field_slots (anchor + variable)   ->  doc_template_slots
--                                                  (anchor -> variable FK)
--   documents.fill_strategy                    ->  (nothing: there is one fill
--                                                  path now, so there is
--                                                  nothing left to choose)
--
-- `documents.fill_strategy` in particular exists only to pick between the
-- slots renderer and docxtpl. The docxtpl path has been deleted — a customer's
-- .docx is never parsed as template source any more — so the column can only
-- ever hold one meaningful value.
--
-- This is deliberately destructive
-- --------------------------------
-- Existing tagged templates lose their field mappings and must be re-uploaded
-- into the new library. That is the agreed cutover: a data migration was
-- considered and rejected, because templates whose `fill_strategy` is NULL
-- have `{{ }}` tags baked into their stored .docx by the retired rewrite step,
-- and there is no honest way to convert those into positional anchors. Half a
-- migration is worse than none — it would silently produce templates that look
-- converted and render wrong.
--
-- The uploaded FILES are untouched. Only the metadata rows and columns go.
--
-- Idempotent — every statement re-runnable.


-- ── 1. template_field_slots ────────────────────────────────────────────────
-- Superseded by doc_template_slots + doc_template_variables. Nothing reads
-- this table after the code change that accompanies this migration.

DROP TABLE IF EXISTS template_field_slots CASCADE;


-- ── 2. documents.fill_strategy ─────────────────────────────────────────────
-- Selected between two renderers. There is one renderer now.

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_fill_strategy_check;
ALTER TABLE documents DROP COLUMN IF EXISTS fill_strategy;


-- ── 3. documents.template_kind / template_status ──────────────────────────
-- These made a knowledge-base document pretend to be a template. A template is
-- now its own row in doc_templates, with versions, so a KB document is once
-- again just a KB document.
--
-- Dropping template_kind also retires the CHECK constraint that made every new
-- customer document type a migration (076 widened it; 092 narrowed it back).
-- document_types replaces it with rows.

DROP INDEX IF EXISTS idx_documents_template_kind;
DROP INDEX IF EXISTS idx_documents_template_active;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_kind_check;
ALTER TABLE documents DROP COLUMN IF EXISTS template_kind;

ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_template_status_check;
ALTER TABLE documents DROP COLUMN IF EXISTS template_status;


-- ── 4. onboarding_runs.status ─────────────────────────────────────────────
-- Unchanged set, restated here so the constraint is re-asserted after the
-- columns above disappear. `blocked_template_drift` stays: the new renderer
-- raises the same condition (a confirmed anchor's paragraph changed) and the
-- agent still parks the run on it.

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
      'blocked_missing_template', 'blocked_template_drift',
      'failed', 'cancelled'
    )
  );


-- ── 5. Restate what onboarding_documents.source_template_id points at ─────
-- It used to reference a `documents` row tagged with a template_kind. It now
-- carries a `doc_templates` id. No FK either way — the column is historical
-- provenance on a row that must survive the template being archived.

COMMENT ON COLUMN onboarding_documents.source_template_id IS
  'The doc_templates row this artifact was generated from. Provenance only, '
  'deliberately unconstrained: the artifact must remain readable after the '
  'template is archived or superseded by a new version. The authoritative, '
  'versioned record of a generation lives in generated_documents.';
