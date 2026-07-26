-- ── 097: Cut over to the slots fill pipeline ───────────────────────────────
--
-- Migration 096 introduced `template_field_slots` alongside the old
-- "bake {{ tags }} into the customer's .docx and render it with docxtpl"
-- pipeline, gated behind `organizations.onboarding_template_pipeline` so a
-- single org could pilot it. The pilot is over: slots is now the only way a
-- detected blank becomes a fill-point, so the gate has nothing left to gate.
--
-- What this changes
-- -----------------
--   * `organizations.onboarding_template_pipeline` is dropped. Every fresh
--     analyze of a document without valid `{{ }}` tags now produces slots,
--     for every org.
--
--   * `documents.fill_strategy` keeps all three states, but their meanings
--     narrow — see the updated COMMENT below. Nothing is rewritten: the
--     column is a record of a decision already made per template, and
--     re-deciding it for a live template is exactly what this pipeline was
--     built to stop doing behind HR's back.
--
-- What this deliberately does NOT do
-- ----------------------------------
-- No backfill of `fill_strategy`. A NULL row is a template that either was
-- never analyzed, or was analyzed under the old pipeline and therefore has
-- real `{{ }}` tags baked into its stored .docx — and only docxtpl can fill
-- those. Setting them to 'slots' would render every one of them unfilled,
-- silently. They keep working as they are; opening the mapper on one
-- re-analyzes it and lands it on 'jinja' or 'slots' honestly.
--
-- Idempotent — every statement re-runnable.

-- ── 1. Retire the rollout gate ─────────────────────────────────────────────

ALTER TABLE organizations DROP CONSTRAINT IF EXISTS organizations_template_pipeline_check;
ALTER TABLE organizations DROP COLUMN IF EXISTS onboarding_template_pipeline;


-- ── 2. Restate what fill_strategy means now ────────────────────────────────
-- The CHECK is unchanged (NULL | 'jinja' | 'slots'); only the semantics of
-- each value have narrowed now that nothing writes 'jinja' except genuine
-- hand-authored templates.

COMMENT ON COLUMN documents.fill_strategy IS
  'How this template gets filled. slots = fill-points live in '
  'template_field_slots and are spliced in directly, with no templating '
  'engine involved; this is what every analyze produces for an ordinary HR '
  'document. jinja = the document was hand-authored with syntactically VALID '
  '{{ }} tags and is rendered by docxtpl as-is. NULL = legacy: never '
  'analyzed, or analyzed under the pre-097 pipeline that baked {{ }} tags '
  'into the stored .docx. NULL still renders via docxtpl so those templates '
  'keep working, but no code path produces NULL any more except a wholesale '
  '.docx replace, which forces a re-analyze.';
