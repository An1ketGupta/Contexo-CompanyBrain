-- ── 108: Turn the step engine on — every step composable, none hardcoded ───
--
-- Why this migration exists
-- -------------------------
-- 107 built the catalog and ran it in shadow: `onboarding_run_steps` tracked
-- what the agent was doing, but `OnboardingV2Agent.run()` still dispatched off
-- an if/elif ladder over `onboarding_runs.status`. That meant the catalog could
-- describe a pipeline the agent would not actually run — a step placed at a new
-- position, or given a different signer list, was recorded and then ignored.
--
-- This migration removes the last three things that kept the ladder in charge.
--
--   1. `onboarding_runs.status` stops being a closed vocabulary. It has been
--      widened by CHECK five times already (071, 074, 075, 080, …) and every
--      org-named step would demand another. The granular truth now lives in
--      `onboarding_run_steps.status`; the run status stays as a coarse label
--      the existing dashboards read, carrying the familiar legacy values while
--      a built-in step is running and a generic one otherwise.
--
--   2. `system`-kind steps gain `system_action`, which says *what* the step
--      does (`bgv`, `policies`) independently of what it is *called*. Before
--      this the agent inferred behaviour from `step_key`, so an org could not
--      rename "Background verification" without breaking it, let alone run two
--      reference checks at different points.
--
--   3. The letter of intent stops being locked. It was pinned first because the
--      steps after it read state it wrote — the candidate account, the
--      references token. Neither is true any more: 107 moved account
--      provisioning to run creation, and the BGV step now mints its own
--      references token instead of inheriting one from the LOI email. So the
--      LOI becomes an ordinary document step an org may move, or not run.
--
-- Idempotent — every statement re-runnable.


-- ── 1. onboarding_runs.status: coarse label, not a state machine ───────────
-- Dropped rather than widened. The step rows are authoritative about where a
-- run is; this column exists so a list view can say "Awaiting signature"
-- without joining. Constraining it would only mean another migration the next
-- time an org invents a step, which is the churn this whole effort removes.

ALTER TABLE onboarding_runs DROP CONSTRAINT IF EXISTS onboarding_runs_status_check;

COMMENT ON COLUMN onboarding_runs.status IS
  'Coarse run label for list views. Legacy ladder values (loi_pending_hr_review, '
  'bgv_pending, …) are still written while a built-in step is running, so '
  'existing dashboards keep working; org-defined steps write generic values '
  '(step_generating, step_pending_hr_review, step_pending_signature, '
  'awaiting_candidate_documents, step_active). The authoritative per-step state '
  'is onboarding_run_steps.status.';

-- Which step the run is actually sitting on. Denormalized from the run steps so
-- the runs list can render a stage without a second query per row.
ALTER TABLE onboarding_runs
  ADD COLUMN IF NOT EXISTS active_step_key TEXT;


-- ── 2. system_action: what a system step does, not what it is called ───────
-- Only `system`-kind steps carry one. `generate` steps are identified by
-- `document_type_key` and `collect` steps by their checklist, so neither needs
-- a discriminator.

ALTER TABLE onboarding_step_defs
  ADD COLUMN IF NOT EXISTS system_action TEXT;

ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS system_action TEXT;

ALTER TABLE onboarding_step_defs DROP CONSTRAINT IF EXISTS onboarding_step_defs_system_action_check;
ALTER TABLE onboarding_step_defs
  ADD CONSTRAINT onboarding_step_defs_system_action_check CHECK (
    (kind = 'system' AND system_action IN ('bgv', 'policies'))
    OR (kind <> 'system' AND system_action IS NULL)
  );

COMMENT ON COLUMN onboarding_step_defs.system_action IS
  'What a system-kind step does: bgv (collect references, email each one a '
  'verification form) or policies (assign acknowledgements and wait). Separate '
  'from step_key so an org can rename or duplicate the step without changing '
  'which handler runs it.';

-- Backfill from the seeded keys. The only system steps that exist before this
-- migration are the two 107 seeded, and their step_key was their behaviour.
UPDATE onboarding_step_defs
   SET system_action = step_key
 WHERE kind = 'system'
   AND system_action IS NULL
   AND step_key IN ('bgv', 'policies');

UPDATE onboarding_run_steps
   SET system_action = step_key
 WHERE kind = 'system'
   AND system_action IS NULL
   AND step_key IN ('bgv', 'policies');


-- ── 3. Unlock the letter of intent ─────────────────────────────────────────
-- `locked` stays on the table — it is the right mechanism if a step ever again
-- has to lead — but nothing uses it today. An org that wants to open with a
-- document request rather than an LOI can now say so.

UPDATE onboarding_step_defs SET locked = FALSE WHERE locked = TRUE;

COMMENT ON COLUMN onboarding_step_defs.locked IS
  'Reserved. A locked step may be neither disabled nor removed. No step is '
  'locked as of 108 — the LOI was, until run-creation took over the candidate '
  'account and the BGV step took over the references token.';


-- ── 4. Signer roles on collect and system steps are meaningless ────────────
-- Nothing ever set them, but the column defaulted to '{}' rather than being
-- constrained, and a stray value would silently route a signing envelope for a
-- step that produces no document.

ALTER TABLE onboarding_step_defs DROP CONSTRAINT IF EXISTS onboarding_step_defs_signer_roles_check;
ALTER TABLE onboarding_step_defs
  ADD CONSTRAINT onboarding_step_defs_signer_roles_check CHECK (
    kind = 'generate' OR signer_roles = '{}'::text[]
  );

UPDATE onboarding_step_defs
   SET signer_roles = '{}'::text[]
 WHERE kind <> 'generate'
   AND signer_roles <> '{}'::text[];


-- ── 5. Runs whose steps were never materialized ────────────────────────────
-- 107's backfill deliberately skipped `failed` and `cancelled` runs, and any
-- blocked run whose blocked_template_kind was empty. Those are terminal or
-- re-materialize on their next kick, so nothing to do — but a run that is
-- neither, and still has no step rows, would now dispatch into an empty
-- pipeline and complete instantly. Park those explicitly rather than letting
-- the generic dispatcher declare them finished.

UPDATE onboarding_runs r
   SET status = 'draft',
       active_step_key = NULL
 WHERE r.status NOT IN ('failed', 'cancelled', 'completed', 'draft')
   AND NOT EXISTS (
     SELECT 1 FROM onboarding_run_steps rs WHERE rs.run_id = r.id
   );
