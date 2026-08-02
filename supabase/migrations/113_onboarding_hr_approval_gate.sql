-- ── 113: HR approves what the candidate did, before the run moves on ──────
--
-- Why this migration exists
-- -------------------------
-- Every point where the candidate acts, the pipeline took their word for it.
-- They signed the appointment letter — the agent delivered it and advanced. They
-- uploaded ten joining documents — the run stepped past the moment the last file
-- landed, and `onboarding_collect_submissions.review_status` recorded HR's
-- opinion afterwards without ever gating anything (107 said so in as many
-- words). They named their referees — verification emails went out unread.
--
-- That was a deliberate call: "a hire should not stall because a review queue
-- went unattended". It is the wrong call for signatures. A signature in the
-- wrong box, an unsigned page, a marksheet photographed at an angle nobody can
-- read — these are not observations to file after the fact, they are reasons to
-- ask again, and by the time HR sees them the run has moved two steps on and
-- there is no way back short of cancelling the hire.
--
-- So: after the candidate acts, the step parks in `pending_hr_approval`. HR
-- previews what came in and either accepts it — the run carries on — or rejects
-- it with a note, and the candidate is asked for the same thing again, at the
-- same step, without the pipeline rewinding past it.
--
-- The shape here
-- --------------
--   onboarding_step_defs.requires_hr_approval    per-step, default on
--   onboarding_run_steps.requires_hr_approval    snapshotted with the rest
--   onboarding_run_steps.review_*                the live decision + note
--   onboarding_run_steps.approval_round          how many times sent back
--
-- `pending_hr_approval` and `submitted` are already in the run-step status
-- CHECK — 107 reserved them for exactly this and nothing has written one since.
-- No constraint change is needed to start using them.
--
-- Idempotent — every statement re-runnable.


-- ── 1. The toggle, on the catalog and on the snapshot ──────────────────────
-- Per-step rather than per-org: an org that wants its NDA countersignature
-- checked but not its induction pack acknowledgement says so in Settings, the
-- same way it already says who signs what. Default TRUE because the failure it
-- prevents (a wrongly-signed document delivered as final) is worse than the one
-- it causes (a step waiting on HR).

ALTER TABLE onboarding_step_defs
  ADD COLUMN IF NOT EXISTS requires_hr_approval BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN onboarding_step_defs.requires_hr_approval IS
  'Whether HR must accept what the candidate did before the run advances past '
  'this step. Applies to a signed document, a collect checklist, and the '
  'referee list a BGV step gathers. Snapshotted onto onboarding_run_steps at '
  'run creation like every other catalog field.';

-- Runs already in flight keep the behaviour they started with. A candidate who
-- signed last week under the old rules must not have their finished step
-- reopened into a review queue, and a run sitting in `pending_signature` right
-- now would otherwise gain a gate that was never announced to anyone. The
-- snapshot principle 107 established says this out loud: a catalog edit cannot
-- change what a run already underway does.
--
-- The grandfathering has to happen exactly once, in the same breath as the
-- column appearing — every row that exists at that instant, and only those,
-- predates the gate. As a bare `ADD COLUMN IF NOT EXISTS` plus an unguarded
-- `UPDATE … SET FALSE`, a second run of this file would silently strip the
-- gate off every live run in the system, which is the opposite of idempotent.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'onboarding_run_steps'
       AND column_name = 'requires_hr_approval'
  ) THEN
    ALTER TABLE onboarding_run_steps
      ADD COLUMN requires_hr_approval BOOLEAN NOT NULL DEFAULT TRUE;
    UPDATE onboarding_run_steps SET requires_hr_approval = FALSE;
  END IF;
END $$;


-- ── 2. The decision itself ─────────────────────────────────────────────────
-- Held on the run step rather than in a separate audit table. The timeline
-- (`onboarding_events`) is already where the history of a run is read, and it
-- gets a row per decision; what the step needs is only the current answer —
-- whether this round of the candidate's work has been accepted.

ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS review_decision TEXT;

ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS review_note TEXT;

ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

-- Zero until HR sends something back. Carried into signing-envelope and email
-- dedupe keys so the second ask is a new envelope and a new email rather than a
-- replay of the first, which both the esign adapter and the email worker would
-- otherwise suppress as a duplicate.
ALTER TABLE onboarding_run_steps
  ADD COLUMN IF NOT EXISTS approval_round INTEGER NOT NULL DEFAULT 0;

ALTER TABLE onboarding_run_steps DROP CONSTRAINT IF EXISTS onboarding_run_steps_review_decision_check;
ALTER TABLE onboarding_run_steps
  ADD CONSTRAINT onboarding_run_steps_review_decision_check CHECK (
    review_decision IS NULL OR review_decision IN ('approved', 'rejected')
  );

COMMENT ON COLUMN onboarding_run_steps.review_decision IS
  'HR''s answer to the current round of candidate work: approved, rejected, or '
  'NULL for not yet reviewed. Reset to NULL when the candidate acts again, so '
  'the gate closes behind every resubmission rather than only the first.';

COMMENT ON COLUMN onboarding_run_steps.review_note IS
  'Why HR sent it back. Shown to the candidate verbatim — it is the only '
  'instruction they get about what to fix.';

-- The review queue: every step waiting on HR, across every run in the org.
CREATE INDEX IF NOT EXISTS idx_onboarding_run_steps_pending_approval
  ON onboarding_run_steps (org_id, status)
  WHERE status = 'pending_hr_approval';


-- ── 3. A rejected submission stops counting as filed ───────────────────────
-- `is_collect_satisfied` asks whether a row exists for each required item.
-- Rejecting one used to leave that row in place, so the checklist stayed
-- satisfied and the "please re-upload" ask had nothing holding it open. The
-- gate now reads `review_status <> 'rejected'`, and this index is what keeps
-- that read cheap on a run with a long checklist.

CREATE INDEX IF NOT EXISTS idx_onboarding_collect_submissions_step_status
  ON onboarding_collect_submissions (run_step_id, review_status);


-- ── 4. Superseded referees ─────────────────────────────────────────────────
-- When HR rejects the referee list — a personal email address where a manager
-- was asked for, a referee who is the candidate's brother — the candidate names
-- new ones. The old rows are not deleted: HR rejecting a reference is a thing
-- that happened to this hire and the timeline should still be able to point at
-- it. They are marked instead, and every read that decides whether verification
-- is outstanding ignores them.
--
-- Only reachable before the verification emails go out; the gate sits between
-- the candidate submitting the list and the agent mailing it.

ALTER TABLE onboarding_bgv_references DROP CONSTRAINT IF EXISTS onboarding_bgv_references_status_check;
ALTER TABLE onboarding_bgv_references
  ADD CONSTRAINT onboarding_bgv_references_status_check CHECK (
    status IN ('pending', 'sent', 'opened', 'submitted', 'expired', 'superseded')
  );

COMMENT ON COLUMN onboarding_bgv_references.status IS
  'pending → sent → opened → submitted, or expired when the token lapses. '
  '`superseded` is a referee HR rejected before verification was requested; the '
  'row is kept for the timeline and ignored by every completeness check.';
